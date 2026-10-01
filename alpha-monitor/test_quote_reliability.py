import json
import unittest
from unittest.mock import patch

import alerts
import cloud
import dingtalk
import positions
import quotes
import fast_lane
from test_strategy import Journal, NOW, ADDRESS, TOKEN


class QuoteSources(unittest.TestCase):
    def minute(self, start=None, trades=1, volume=10):
        start = int(NOW*1000)//60000*60000-60000 if start is None else start
        return [start, 10, 11, 9, 10.5, volume, start+59999, 100, trades]

    def test_no_trade_candles_never_refresh_old_price(self):
        with patch('quotes.time.time', return_value=NOW):
            def fetch(url, params):
                if url.endswith('/ticker'):
                    return {'lastPrice': 10, 'closeTime': (NOW-1200)*1000}
                return [self.minute(trades=0, volume=0)]
            quote, detail = quotes.get_quote(fetch, 'ALPHA_1', fallback=True)
        self.assertIsNone(quote)
        self.assertEqual(detail['age_seconds'], 1200)
        self.assertEqual(detail['fallback'], 'no_recent_traded_minute')

    def test_traded_minute_is_conservative_fallback(self):
        row = self.minute()
        with patch('quotes.time.time', return_value=NOW):
            def fetch(url, params):
                if url.endswith('/ticker'):
                    return {'lastPrice': 10, 'closeTime': (NOW-900)*1000}
                return [row]
            quote, detail = quotes.get_quote(fetch, 'ALPHA_1', fallback=True)
        self.assertEqual(quote['time'], row[0])
        self.assertEqual(quote['source'], 'alpha_1m_traded')
        self.assertEqual(detail['reason'], 'ok')

    def test_stale_future_invalid_minutes_rejected(self):
        for row in [self.minute(start=(NOW-240)*1000), self.minute(start=(NOW+60)*1000),
                    self.minute(volume=0), self.minute(trades=0)]:
            self.assertIsNone(quotes.minute_quote([row], NOW))
        bad=self.minute();bad[6]+=1
        self.assertIsNone(quotes.minute_quote([bad], NOW))

    def test_fresh_ticker_does_not_request_fallback(self):
        calls=[]
        def fetch(url, params):
            calls.append(url)
            return {'symbol':'ALPHA_1USDT','lastPrice':10,'closeTime':NOW*1000}
        with patch('quotes.time.time', return_value=NOW):
            q,_=quotes.get_quote(fetch,'ALPHA_1',fallback=True)
        self.assertEqual(len(calls),1);self.assertEqual(q['source'],'alpha_ticker')

    def test_wrong_symbol_and_network_error_do_not_create_price(self):
        for fetch in [lambda *_: {'symbol':'OTHERUSDT','lastPrice':10,'closeTime':NOW*1000},
                      lambda *_: None]:
            with patch('quotes.time.time', return_value=NOW):
                self.assertIsNone(quotes.get_quote(fetch,'ALPHA_1')[0])


class PositionIncidents(unittest.TestCase):
    setUp=Journal.setUp
    tearDown=Journal.tearDown
    command=Journal.command

    def test_ack_sustained_failure_recovery_and_new_incident(self):
        self.command('/buy '+ADDRESS+' 10')
        with self.store.db() as db:
            p=positions.open_positions(db)[0]
            positions.missing_position(db,p,NOW)
            self.assertEqual(db.execute('SELECT count(*) FROM important_alerts').fetchone()[0],0)
            positions.missing_position(db,p,NOW+120)
            aid=db.execute('SELECT id FROM important_alerts').fetchone()[0]
        self.command('/ack '+str(aid))
        with self.store.db() as db:
            positions.missing_position(db,p,NOW+86401)
            self.assertEqual(db.execute('SELECT count(*) FROM important_alerts').fetchone()[0],1)
            positions.position_tick(db,p,{'price':10,'time':(NOW+86402)*1000},200000,NOW+86402)
            positions.position_tick(db,p,{'price':10,'time':(NOW+86403)*1000},200000,NOW+86403)
            self.assertEqual(db.execute("SELECT count(*) FROM outbox WHERE key LIKE 'position-quote-recovered:%'").fetchone()[0],1)
            positions.missing_position(db,p,NOW+87000)
            positions.missing_position(db,p,NOW+87120)
            self.assertEqual(db.execute('SELECT count(*) FROM important_alerts').fetchone()[0],2)

    def test_slow_scan_cannot_overwrite_new_quote_or_warn(self):
        self.command('/buy '+ADDRESS+' 10')
        with self.store.db() as db:
            p=positions.open_positions(db)[0]
            positions.position_tick(db,p,{'price':10,'time':NOW*1000},200000,NOW)
            positions.position_tick(db,p,{'price':8,'time':(NOW-60)*1000},200000,NOW+1)
            positions.position_tick(db,p,{'price':8,'time':(NOW-300)*1000},200000,NOW+1)
            positions.missing_position(db,p,NOW+2)
            positions.missing_position(db,p,NOW+125)
            self.assertEqual(db.execute('SELECT last_price FROM positions').fetchone()[0],10)
            self.assertEqual(db.execute('SELECT count(*) FROM important_alerts').fetchone()[0],0)

    def test_reference_failure_is_labelled_and_expiry_does_not_rearm(self):
        with self.store.db() as db:
            positions.reference_position(db,TOKEN,{'price':10,'time':NOW*1000},'reference',NOW)
            p=positions.open_positions(db)[0]
            positions.missing_position(db,p,NOW)
            positions.missing_position(db,p,NOW+120,{'reason':'ticker_stale_or_future','last_quote_time':(NOW-300)*1000})
            text=db.execute('SELECT text FROM important_alerts').fetchone()[0]
            self.assertIn('假定买入',text);self.assertIn('420 秒',text)
        alerts.reminders(self.store,NOW+90000)
        with self.store.db() as db:
            positions.missing_position(db,p,NOW+90001)
            self.assertEqual(db.execute('SELECT count(*) FROM important_alerts').fetchone()[0],1)

    def test_fast_lane_reports_source_failure_and_uses_current_directory_id(self):
        self.command('/buy '+ADDRESS+' 10')
        token=dict(TOKEN,alphaId='ALPHA_2',marketCap=1e12)
        calls=[]
        def fetch(url,params=None):
            calls.append(params['symbol'])
            if url.endswith('/ticker'):return {'lastPrice':10,'closeTime':(NOW-1000)*1000}
            return []
        with patch('fast_lane.time.time',return_value=NOW),patch('fast_lane.directory',return_value=({'observed':NOW,'tokens':{ADDRESS:token}},[])),patch('fast_lane.fetch',side_effect=fetch):
            report=fast_lane.run(self.store)
        self.assertEqual(set(calls),{'ALPHA_2USDT'})
        self.assertEqual(report['position_quotes'][ADDRESS]['fallback'],'no_recent_traded_minute')
        self.assertEqual(report['status'],'partial')

    def test_cancellation_cost_bounded_by_pending_queue_not_history(self):
        dingtalk.initialize(self.store)
        with self.store.db() as db:
            for i in range(2500):
                aid=alerts.raise_alert(db,'old:'+str(i),'old',NOW)
                db.execute("UPDATE important_alerts SET state='resolved' WHERE id=?",(aid,))
            db.execute("UPDATE outbox SET state='sent'")
            db.execute("UPDATE dingtalk_outbox SET state='sent'")
            resolved=alerts.raise_alert(db,'resolved','cancel',NOW)
            acknowledged=alerts.raise_alert(db,'ack','cancel',NOW)
            active=alerts.raise_alert(db,'active','keep',NOW)
            db.execute("UPDATE important_alerts SET state='resolved' WHERE id=?",(resolved,))
            db.execute("UPDATE important_alerts SET state='acknowledged' WHERE id=?",(acknowledged,))
            # This VM instruction budget fails the old historical nested-loop implementation.
            steps=[0]
            def progress():
                steps[0]+=1
                return int(steps[0]>100)
            db.set_progress_handler(progress,1000)
            try:alerts.cancel_pending(db)
            finally:db.set_progress_handler(None,0)
            for table in ('outbox','dingtalk_outbox'):
                pending=db.execute('SELECT key FROM '+table+" WHERE state='pending'").fetchall()
                self.assertEqual([r[0] for r in pending],[f'important:{active}:0'])
                self.assertEqual(db.execute('SELECT count(*) FROM '+table+" WHERE state='sent'").fetchone()[0],2500)

    def test_fast_scheduler_runs_while_hourly_scan_is_busy(self):
        clock=[NOW];starts=[]
        class Stop:
            def is_set(self):return clock[0]>NOW+130
            def set(self):pass
            def wait(self,seconds):clock[0]+=10
        class Scan:
            def __init__(self,config,fast=False):self.fast=fast;self.started=0
            def start(self):self.started=clock[0];starts.append((self.fast,clock[0]))
            def poll(self):return 'ok' if self.fast else None
            def stop(self):pass
        from dataclasses import replace
        config=replace(self.config,data=self.store.folder,enabled=False,ding_enabled=False)
        with patch('cloud.ScanProcess',Scan),patch('cloud.threading.Event',return_value=Stop()),patch('cloud.threading.Thread'),patch('cloud.signal.signal'),patch('cloud.time.time',side_effect=lambda:clock[0]):
            cloud.run(config)
        self.assertEqual(sum(not fast for fast,_ in starts),1)
        self.assertGreaterEqual(sum(fast for fast,_ in starts),3)


if __name__=='__main__':unittest.main()
