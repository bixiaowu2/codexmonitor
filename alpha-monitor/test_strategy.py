import argparse,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import cloud,positions,strategy,telegram_commands,safety

ADDRESS='0x'+'a'*40
NOW=1800000000.
TOKEN={'symbol':'TEST','alphaId':'ALPHA_1','contractAddress':ADDRESS,'chainId':'56','marketCap':1e7,'fdv':2e7,'liquidity':200000,'volume24h':200000,'percentChange24h':5,'price':10.3,'listingTime':1}

def feature(end=1000,**kw):
    d={'end':end,'open':10,'close':10.3,'low':10,'high':10.4,'volume_ratio':3.,'quote_volume':20000,'level':10,'return_1h':.03,'return_24h':.2};d.update(kw);return d

class Rules(unittest.TestCase):
    def test_unclosed_and_gap_hours_not_accepted(self):
        end=int(NOW*1000)//strategy.HOUR*strategy.HOUR-1
        bars=[{'t':end+1-(25-i)*strategy.HOUR,'end':end-(24-i)*strategy.HOUR,'o':10,'h':11,'l':9,'c':10,'q':1000} for i in range(25)]
        self.assertIsNotNone(strategy.hourly_features(bars,NOW))
        self.assertIsNone(strategy.hourly_features(bars[:-1],NOW))
        bars[5]['t']-=1;self.assertIsNone(strategy.hourly_features(bars,NOW))
    def test_breakout_retest_requires_later_observed_candle(self):
        q={'price':10.3};s,e=strategy.transition({},feature(),q,[],NOW)
        self.assertEqual([x[0] for x in e],['breakout'])
        s,e=strategy.transition(s,feature(),q,[],NOW+60);self.assertEqual(e,[])
        s,e=strategy.transition(s,feature(1000+strategy.HOUR,low=9.99,close=10.1),{'price':10.1},[],NOW+3600)
        self.assertEqual([x[0] for x in e],['retest'])
    def test_risk_or_breakdown_invalidates_once(self):
        s,_=strategy.transition({},feature(),{'price':10.3},[],NOW)
        s,e=strategy.transition(s,None,{'price':9.4},[],NOW+60)
        self.assertEqual(e[0][0],'invalidated')
        s,e=strategy.transition(s,None,{'price':9.4},[],NOW+120);self.assertEqual(e,[])
    def test_no_chasing_or_unsafe_breakout(self):
        for q,blocks in [({'price':12},[]),({'price':10.3},['honeypot'])]:
            self.assertEqual(strategy.transition({},feature(),q,blocks,NOW)[1],[])
    def test_unknown_valuation_blocks(self):
        self.assertEqual(strategy.gates(TOKEN),[])
        self.assertTrue(strategy.gates(dict(TOKEN,fdv=None)))
    def test_stale_or_future_quote_rejected(self):
        for stamp in [(NOW-181)*1000,(NOW+61)*1000]:
            with self.assertRaises(ValueError):strategy.fresh_quote({'lastPrice':'1','closeTime':stamp},NOW)
    def test_raw_holdings_not_claimed_clean(self):
        risk=safety.summarize({'is_honeypot':'0','holders':[{'percent':'.05'}]*10})
        self.assertAlmostEqual(risk['raw_top10_share'],.5);self.assertEqual(risk['cleaned_concentration'],'unknown')
        self.assertTrue(safety.summarize({'is_mintable':'1'})['flags'])
    def test_unknown_honeypot_is_not_safe(self):
        self.assertEqual(safety.summarize({})['status'],'unknown')

class Journal(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=cloud.Store(self.tmp.name)
        with self.store.db() as db:positions.initialize(db)
        self.config=cloud.Config.load({'TELEGRAM_BOT_TOKEN':'123:FAKE','TELEGRAM_CHAT_ID':'1234','RADAR_DATA':self.tmp.name})
        Path(self.tmp.name,'strategy-directory.json').write_text(json.dumps({'observed':NOW,'tokens':{ADDRESS:TOKEN}}))
    def tearDown(self):self.tmp.cleanup()
    def command(self,text,key='test'):
        with self.store.db() as db:return positions.handle_command(db,text,self.tmp.name,key,NOW)
    def update(self,uid=1,text=None,sender=1234,chat=1234,stamp=NOW):
        return {'update_id':uid,'message':{'date':stamp,'text':text or '/buy '+ADDRESS+' 10','chat':{'id':chat,'type':'private'},'from':{'id':sender}}}
    def test_command_idempotency_and_authorization(self):
        telegram_commands.process_updates(self.store,self.config,[self.update()],NOW)
        telegram_commands.process_updates(self.store,self.config,[self.update(),self.update(2,sender=999),self.update(3,chat=999)],NOW)
        with self.store.db() as db:
            self.assertEqual(len(positions.open_positions(db)),1)
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0],1)
        self.assertEqual(self.store.state('telegram_offset'),4)
    def test_command_mutation_reply_and_offset_rollback(self):
        with patch('telegram_commands.emit',side_effect=RuntimeError('simulate DB failure')):
            with self.assertRaises(RuntimeError):telegram_commands.process_updates(self.store,self.config,[self.update()],NOW)
        with self.store.db() as db:self.assertEqual(len(positions.open_positions(db)),0)
        self.assertIsNone(self.store.state('telegram_offset'))
    def test_old_commands_and_forwarded_commands_ignored(self):
        forwarded=self.update(2);forwarded['message']['forward_origin']={'type':'user'}
        telegram_commands.process_updates(self.store,self.config,[self.update(stamp=NOW-601),forwarded],NOW)
        with self.store.db() as db:self.assertEqual(len(positions.open_positions(db)),0)
    def test_invalid_and_duplicate_positions(self):
        for price in ('nan','inf','0','-1'):
            self.assertIn('无效',self.command('/buy '+ADDRESS+' '+price))
        self.assertIn('已登记',self.command('/buy '+ADDRESS+' 10'))
        self.assertIn('已登记为',self.command('/buy '+ADDRESS+' 12','test2'))
    def test_exits_persist_and_high_water_is_not_historical_high(self):
        self.command('/buy '+ADDRESS+' 10')
        with self.store.db() as db:
            p=positions.open_positions(db)[0]
            self.assertEqual(p['peak'],10)
            positions.position_tick(db,p,{'price':16,'time':NOW*1000},200000,NOW)
        restarted=cloud.Store(self.tmp.name)
        with restarted.db() as db:
            p=positions.open_positions(db)[0];positions.position_tick(db,p,{'price':12,'time':(NOW+1)*1000},200000,NOW+1)
            p=positions.open_positions(db)[0];positions.position_tick(db,p,{'price':12,'time':(NOW+2)*1000},200000,NOW+2)
            self.assertEqual(db.execute('SELECT count(*) FROM position_alerts').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0],2)
        self.assertIn('已结束',self.command('/close 1'))
    def test_stop_and_missing_quotes_are_separate(self):
        self.command('/buy '+ADDRESS+' 10')
        with self.store.db() as db:
            p=positions.open_positions(db)[0];positions.missing_position(db,p,NOW)
            self.assertEqual(db.execute('SELECT count(*) FROM position_alerts').fetchone()[0],0)
            positions.position_tick(db,p,{'price':8,'time':NOW*1000},200000,NOW)
            self.assertEqual(db.execute('SELECT kind FROM position_alerts').fetchone()[0],'stop')
    def test_paper_horizon_does_not_backfill_missed_price(self):
        with self.store.db() as db:
            db.execute('INSERT INTO paper_tracks(key,address,symbol,created,entry,last,peak,trough,checked) VALUES(?,?,?,?,?,?,?,?,?)',('x',ADDRESS,'TEST',NOW-26*3600,10,10,10,10,NOW-26*3600))
            strategy.paper_tick(db,ADDRESS,{'price':20},NOW)
            marks=json.loads(db.execute('SELECT marks FROM paper_tracks').fetchone()[0]);self.assertEqual(marks['24'],{'missing':True})
    def test_unverified_or_stale_unlock_is_unknown(self):
        p=Path(self.tmp.name)/'risk-overrides.json'
        p.write_text(json.dumps({ADDRESS:{'source':'https://example.org/disclosure','reviewed_at':NOW-86400,'unlock_at':NOW+86400,'fraction_of_circulating':.06}}))
        self.assertTrue(safety.unlock_override(ADDRESS,self.tmp.name,NOW)['large_unlock_soon'])
        self.assertEqual(safety.unlock_override(ADDRESS,self.tmp.name,NOW+8*86400)['status'],'unknown')


class Pipeline(unittest.TestCase):
    setUp=Journal.setUp
    tearDown=Journal.tearDown
    command=Journal.command
    def fake_api(self,fail_directory=False):
        class API:
            def __init__(self,*a,**kw):pass
            def data(self,url,params=None):
                if url==strategy.TOKENS:
                    if fail_directory:raise RuntimeError('directory unavailable')
                    return [TOKEN]
                if url.endswith('exchangeInfo'):return {'symbols':[{'symbol':'OTHERUSDT','baseAsset':'OTHER','quoteAsset':'USDT','contractType':'PERPETUAL','status':'TRADING'}]}
                if url.endswith('/ticker'):return {'lastPrice':8 if fail_directory else 10.3,'closeTime':NOW*1000}
                if url.endswith('/klines'):
                    end=int(NOW*1000)//strategy.HOUR*strategy.HOUR-1
                    rows=[]
                    for i in range(25):
                        start=end+1-(25-i)*strategy.HOUR;last=i==24
                        rows.append([start,10 if last else 9.9,10.4 if last else 10,9.9 if last else 9.8,10.3 if last else 9.9,100,start+strategy.HOUR-1,15000 if last else 5000,10])
                    return rows
                raise ValueError(url)
        return API
    def test_complete_pipeline_dedup_and_paper_entry(self):
        args=argparse.Namespace(data=self.tmp.name,hourly_enrich=3)
        with patch('strategy.time.time',return_value=NOW),patch('strategy.PublicAPI',self.fake_api()),patch('strategy.inspect',return_value={'status':'no_listed_risk_detected','flags':[]}):
            report=strategy.scan(args,self.store)
            self.assertEqual(report['status'],'ok');self.assertEqual(report['alerts'][0]['kind'],'breakout')
            again=strategy.scan(args,self.store);self.assertEqual(again['alerts'],[])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT entry FROM paper_tracks').fetchone()[0],10.3)
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0],1)
    def test_risk_provider_flag_suppresses_entry(self):
        args=argparse.Namespace(data=self.tmp.name,hourly_enrich=3)
        with patch('strategy.time.time',return_value=NOW),patch('strategy.PublicAPI',self.fake_api()),patch('strategy.inspect',return_value={'status':'risk_detected','flags':['honeypot']}):
            self.assertEqual(strategy.scan(args,self.store)['alerts'],[])
    def test_existing_position_exit_survives_directory_failure(self):
        self.command('/buy '+ADDRESS+' 10');args=argparse.Namespace(data=self.tmp.name,hourly_enrich=3)
        with patch('strategy.time.time',return_value=NOW),patch('strategy.PublicAPI',self.fake_api(True)),patch('strategy.inspect',return_value={'status':'unknown','flags':[]}):
            report=strategy.scan(args,self.store);self.assertEqual(report['status'],'partial')
        with self.store.db() as db:self.assertEqual(db.execute('SELECT kind FROM position_alerts').fetchone()[0],'stop')
    def test_institution_claim_never_adds_score_or_control_certainty(self):
        Path(self.tmp.name,'risk-overrides.json').write_text(json.dumps({ADDRESS:{'entities':[{'name':'Example investor','role':'investor','source':'https://example.org/disclosure','reviewed_at':NOW}]}}))
        c=safety.entity_evidence(ADDRESS,self.tmp.name,NOW)[0]
        self.assertEqual(c['score_contribution'],0);self.assertIn('not_independently_verified',c['confidence'])

    def test_stale_quote_advances_rotation_without_false_alert(self):
        parent=self.fake_api()
        class StaleAPI(parent):
            def data(self,url,params=None):
                if url.endswith('/ticker'):return {'lastPrice':10.3,'closeTime':(NOW-600)*1000}
                return super().data(url,params)
        with patch('strategy.time.time',return_value=NOW),patch('strategy.PublicAPI',StaleAPI):
            r=strategy.scan(argparse.Namespace(data=self.tmp.name,hourly_enrich=3),self.store)
        self.assertEqual(len(r['skipped']),1);self.assertEqual(r['alerts'],[])
        with self.store.db() as db:self.assertEqual(json.loads(db.execute('SELECT payload FROM strategy_state').fetchone()[0])['checked'],NOW)

if __name__=='__main__':unittest.main()
