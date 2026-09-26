import json,tempfile,unittest,time
from pathlib import Path
from unittest.mock import patch
import cloud,positions,alerts,evaluation,fast_lane,strategy,telegram_commands
from test_strategy import TOKEN,ADDRESS,NOW
import test_strategy as fixtures

class ReferenceAndAck(unittest.TestCase):
    setUp=fixtures.Journal.setUp
    tearDown=fixtures.Journal.tearDown
    command=fixtures.Journal.command
    def reference(self):
        with self.store.db() as db:return positions.reference_position(db,TOKEN,{'price':10,'time':NOW*1000},'signal:1',NOW)
    def test_reference_is_explicit_and_never_duplicates(self):
        self.assertIn('未核实成交',self.reference());self.reference()
        with self.store.db() as db:
            rows=positions.open_positions(db);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['mode'],'reference')
    def test_actual_cost_replaces_reference_and_resets_peak(self):
        self.reference()
        self.assertIn('已登记',self.command('/buy '+ADDRESS+' 12'))
        with self.store.db() as db:
            rows=positions.open_positions(db);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['entry'],12);self.assertEqual(rows[0]['peak'],12);self.assertEqual(rows[0]['mode'],'actual')
            self.assertEqual(db.execute('SELECT count(*) FROM positions').fetchone()[0],2)
    def test_reference_does_not_overwrite_actual_cost(self):
        self.command('/buy '+ADDRESS+' 12');self.reference()
        with self.store.db() as db:self.assertEqual(positions.open_positions(db)[0]['entry'],12)
    def test_critical_repeat_cap_and_ack(self):
        with self.store.db() as db:aid=alerts.raise_alert(db,'test','test critical',NOW)
        for i in range(1,6):alerts.reminders(self.store,NOW+300*i)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0],4)
        self.assertIn('已确认',self.command('/ack '+str(aid)))
        alerts.reminders(self.store,NOW+2000)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0],4)
    def test_reference_stop_message_never_claims_actual_trade(self):
        self.reference()
        with self.store.db() as db:
            p=positions.open_positions(db)[0];positions.position_tick(db,p,{'price':8,'time':NOW*1000},200000,NOW)
            text=json.loads(db.execute('SELECT payload FROM outbox').fetchone()[0])['text']
            self.assertIn('假定买入',text);self.assertIn('/ack',text)
    def test_close_cancels_critical_repeats(self):
        self.reference()
        with self.store.db() as db:
            p=positions.open_positions(db)[0];positions.position_tick(db,p,{'price':8,'time':NOW*1000},200000,NOW)
        self.command('/close 1');alerts.reminders(self.store,NOW+600)
        with self.store.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM important_alerts WHERE state='open'").fetchone()[0],0)
    def test_stop_recovers_and_rearms_new_incident(self):
        self.reference()
        with self.store.db() as db:
            p=positions.open_positions(db)[0];positions.position_tick(db,p,{'price':8,'time':NOW*1000},200000,NOW)
            p=positions.open_positions(db)[0];positions.position_tick(db,p,{'price':10,'time':(NOW+1)*1000},200000,NOW+1)
            p=positions.open_positions(db)[0];positions.position_tick(db,p,{'price':8,'time':(NOW+2)*1000},200000,NOW+2)
            self.assertEqual(db.execute('SELECT count(*) FROM important_alerts').fetchone()[0],2)
    def test_fast_and_main_failure_counts_are_separate(self):
        for _ in range(3):self.store.scan_status('failed',now=NOW,namespace='fast')
        self.store.scan_status('ok',now=NOW,namespace='scan')
        self.assertEqual(self.store.state('fast')['failures'],3);self.assertEqual(self.store.state('scan')['failures'],0)

class Evaluation(unittest.TestCase):
    setUp=fixtures.Journal.setUp
    tearDown=fixtures.Journal.tearDown
    def test_outcome_uses_forward_quotes_and_does_not_fill_missing_horizon(self):
        with self.store.db() as db:
            evaluation.initialize(db)
            evaluation.register(db,'s',ADDRESS,'TEST',{'price':10,'time':NOW*1000},'breakout',{},NOW)
            evaluation.observe(db,ADDRESS,{'price':100,'time':(NOW-1)*1000},NOW+1)
            self.assertEqual(db.execute('SELECT peak FROM evaluation_tracks').fetchone()[0],10)
            evaluation.observe(db,ADDRESS,{'price':20,'time':(NOW+26*3600)*1000},NOW+26*3600)
            r=db.execute('SELECT * FROM evaluation_tracks').fetchone()
            self.assertEqual(r['peak'],20);self.assertEqual(json.loads(r['horizons'])['24'],{'missing':True})
    def test_daily_report_does_not_claim_zero_success_rate_with_no_mature_cases(self):
        with self.store.db() as db:
            evaluation.initialize(db);evaluation.register(db,'s',ADDRESS,'TEST',{'price':10,'time':NOW*1000},'breakout',{},NOW)
        report=evaluation.report(self.store,now=NOW,force=True)
        self.assertEqual(report['groups'][0]['mature_90d'],0)
        self.assertIn('no_automatic_threshold_change',report['optimization_status'])
    def test_large_price_jump_flagged_for_review(self):
        with self.store.db() as db:
            evaluation.initialize(db);evaluation.register(db,'s',ADDRESS,'TEST',{'price':1,'time':NOW*1000},'breakout',{},NOW)
            evaluation.observe(db,ADDRESS,{'price':100,'time':(NOW+60)*1000},NOW+60)
            self.assertEqual(db.execute('SELECT suspect_jump FROM evaluation_tracks').fetchone()[0],1)
    def test_valid_90_day_endpoint_counted_without_future_peeking(self):
        with self.store.db() as db:
            evaluation.initialize(db);evaluation.register(db,'s',ADDRESS,'TEST',{'price':10,'time':NOW*1000},'breakout',{},NOW)
            evaluation.observe(db,ADDRESS,{'price':20,'time':(NOW+90*86400)*1000},NOW+90*86400)
        r=evaluation.report(self.store,now=NOW+90*86400,force=True)
        self.assertEqual(r['groups'][0]['usable_90d'],1)
    def test_quarter_alert_requires_closed_contiguous_candles(self):
        end=int(NOW*1000)//fast_lane.QUARTER*fast_lane.QUARTER-1
        bars=[{'t':end+1-(17-i)*fast_lane.QUARTER,'end':end-(16-i)*fast_lane.QUARTER,'o':10,'c':10.2 if i==16 else 10,'h':10.3 if i==16 else 10.1,'q':6000 if i==16 else 1000} for i in range(17)]
        self.assertIsNotNone(fast_lane.quarter_signal(bars,NOW));bars[3]['t']-=1
        self.assertIsNone(fast_lane.quarter_signal(bars,NOW))

if __name__=='__main__':unittest.main()
