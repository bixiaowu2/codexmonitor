import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import holdings_core as c
import holdings_service as s
import holdings_sources as sources

NOW=1790859600.
ADDRESS='0x'+'a'*40

def position(**kw):
    return dict({'id':1,'domain':'meme','market':'bsc','instrument':ADDRESS,'entry':10.,'peak':10.,'created':NOW-1000,'stop_pct':.15,'trail_pct':.2},**kw)

def bars(count=60):
    return [{'time':NOW-(count-i)*300,'end':NOW-(count-i-1)*300,'open':10+i*.1,'high':10.3+i*.1,'low':9.8+i*.1,'close':10.1+i*.1} for i in range(count)]

class Holdings(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=sqlite3.connect(Path(self.tmp.name)/'holdings.sqlite');self.addCleanup(self.db.close);c.initialize(self.db)
        self.route={'telegram':True,'dingtalk':True,'token':'1:FAKE','chat':'123','webhook':'https://oapi.dingtalk.com/robot/send?access_token=FAKE','signing':'','keyword':'TEST'}
    def command(self,text,domain='meme',key=None):
        with self.db:return c.command(self.db,domain,text,key or domain+':'+text,NOW)
    def test_identity_and_currency_formats(self):
        for domain,market,symbol in [('stock','a','600519.SS'),('stock','hk','0700.HK'),('stock','us','NVDA'),('meme','bsc',ADDRESS)]:
            self.assertEqual(c.identity(domain,market,symbol),(market,symbol))
        for domain,market,symbol in [('stock','a','NVDA'),('stock','us','0700.HK'),('meme','arc','short'),('stock','a','600519')]:
            with self.assertRaises(ValueError):c.identity(domain,market,symbol)
    def test_register_update_not_duplicate_and_no_fabricated_fill(self):
        text=self.command('/buy bsc '+ADDRESS+' 10 200')
        self.assertIn('尚待',text)
        self.command('/buy bsc '+ADDRESS+' 10 200')
        self.command('/buy bsc '+ADDRESS+' 12','meme','update')
        rows=self.db.execute('SELECT * FROM positions').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['entry'],12);self.assertEqual(rows[0]['peak'],12);self.assertIsNone(rows[0]['quantity'])
    def test_invalid_costs_do_not_register(self):
        for value in ('nan','inf','0','-1'):
            self.command('/buy bsc '+ADDRESS+' '+value)
        self.assertEqual(self.db.execute('SELECT count(*) FROM positions').fetchone()[0],0)
    def test_domain_ownership_and_cancel_old_pending_advice(self):
        self.command('/buy bsc '+ADDRESS+' 10')
        with self.db:s.enqueue(self.db,'risk:meme:1:1','meme',self.route,'old',NOW)
        self.assertIn('没有找到',self.command('/close 1','stock'))
        self.assertIn('已结束',self.command('/close 1'))
        self.assertEqual(self.db.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0],0)
    def test_risk_settings_and_trend_lookup(self):
        self.command('/buy us NVDA 100 10','stock')
        self.assertIn('已更新',self.command('/risk 1 10 20','stock'))
        self.assertEqual(self.db.execute('SELECT stop_pct FROM positions').fetchone()[0],.1)
        self.assertIn('等待',self.command('/trend 1','stock'))
    def test_only_recent_owner_private_messages_accepted(self):
        update={'update_id':1,'message':{'date':NOW,'chat':{'id':123,'type':'private'},'from':{'id':123},'text':'/positions'}}
        self.assertTrue(c.authorized_update(update,'123',NOW))
        for field,value in [('from',{'id':999}),('chat',{'id':123,'type':'group'}),('date',NOW-601),('forward_origin',{})]:
            bad=json.loads(json.dumps(update));bad['message'][field]=value
            self.assertFalse(c.authorized_update(bad,'123',NOW))
    def test_poll_command_and_offset_commit_once(self):
        update={'update_id':1,'message':{'date':NOW,'chat':{'id':123,'type':'private'},'from':{'id':123},'text':'/buy bsc '+ADDRESS+' 10'}}
        request=lambda *_:{'ok':True,'result':[update]}
        s.poll(self.db,'meme',self.route,NOW,request);s.poll(self.db,'meme',self.route,NOW,request)
        self.assertEqual(s.state(self.db,'offset:meme'),2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM positions').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM outbox').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT channel FROM outbox').fetchone()[0],'telegram')
    def test_invalid_reply_transport_rolls_back_registration_and_offset(self):
        update={'update_id':1,'message':{'date':NOW,'chat':{'id':123,'type':'private'},'from':{'id':123},'text':'/buy bsc '+ADDRESS+' 10'}}
        with patch('holdings_service.enqueue',side_effect=RuntimeError('db failure')):
            with self.assertRaises(RuntimeError):s.poll(self.db,'meme',self.route,NOW,lambda *_:{'ok':True,'result':[update]})
        self.assertEqual(self.db.execute('SELECT count(*) FROM positions').fetchone()[0],0)
        self.assertIsNone(s.state(self.db,'offset:meme'))
    def test_stale_price_never_triggers_exit(self):
        q={'price':1,'time':NOW-901,'source':'test'}
        result=c.analyze(position(),q,bars(),NOW,'5m')
        self.assertFalse(result['fresh']);self.assertEqual(result['flags'],[]);self.assertEqual(result['peak'],10)
    def test_observed_peak_is_not_historical_high_and_ma_atr_are_available(self):
        history=bars();history[0]['high']=10000
        result=c.analyze(position(),{'price':16,'time':NOW-10},history,NOW,'5m')
        self.assertTrue(result['fresh']);self.assertEqual(result['peak'],16)
        self.assertIsNotNone(result['ma50']);self.assertGreater(result['atr14'],0)
    def test_intraday_gap_refuses_trend_but_keeps_fresh_fixed_stop(self):
        data=bars();data.pop(-4)
        result=c.analyze(position(),{'price':8,'time':NOW-10},data,NOW,'5m')
        self.assertIsNone(result['ma20']);self.assertIn('触及登记止损参考线',result['flags'])
    def test_meme_zero_volume_candles_never_refresh_price(self):
        def fetch(url,params):
            if url.endswith('/pools'):return {'data':[{'attributes':{'address':'pool','reserve_in_usd':10000},'relationships':{'base_token':{'data':{'id':'bsc_'+ADDRESS}}}}]}
            return {'data':{'attributes':{'ohlcv_list':[[NOW-300,10,11,9,10,0]]}}}
        with patch('holdings_sources.time.time',return_value=NOW):
            with self.assertRaises(ValueError):sources.meme_data(position(),fetch)
    def test_wrong_pool_base_is_rejected(self):
        with self.assertRaises(ValueError):sources.meme_data(position(),lambda *_:{'data':[{'attributes':{'address':'pool','reserve_in_usd':10000},'relationships':{'base_token':{'data':{'id':'bsc_wrong'}}}}]})

    def test_stock_identity_and_unfinished_daily_bar(self):
        def chart(symbol):
            return {'chart':{'result':[{'meta':{'symbol':symbol,'currentTradingPeriod':{'regular':{'start':NOW-3600,'end':NOW+3600}}},
                'timestamp':[NOW-3600], 'indicators':{'quote':[{'open':[10],'high':[11],'low':[9],'close':[10],'volume':[100]}]}}]}}
        with self.assertRaises(ValueError):sources.chart_rows(chart('OTHER'),'NVDA',300,NOW)
        self.assertEqual(sources.chart_rows(chart('NVDA'),'NVDA',86400,NOW)[0],[])
        completed=sources.chart_rows(chart('NVDA'),'NVDA',86400,NOW+4000)[0]
        self.assertEqual(completed[0]['end'],NOW+3600)
    def test_notification_channels_independent_and_expired_not_sent(self):
        with self.db:s.enqueue(self.db,'test','meme',self.route,'hello',NOW)
        calls=[]
        def send(route,text,channel):
            calls.append(channel)
            if channel=='dingtalk':raise RuntimeError('fail')
        s.deliver(self.db,{'meme':self.route},NOW,send)
        states={r['channel']:r['state'] for r in self.db.execute('SELECT * FROM outbox')}
        self.assertEqual(states,{'telegram':'sent','dingtalk':'pending'})
        s.deliver(self.db,{'meme':self.route},NOW+1000,lambda *_:self.fail('stale send'))
    def test_unicode_message_chunks_fit_telegram(self):
        original='😀你好'*3000
        pieces=s.chunks(original)
        self.assertEqual(''.join(pieces),original)
        self.assertTrue(all(len(p.encode('utf-16-le'))//2<=3400 for p in pieces))

    def test_cycle_only_actual_positions_with_deduped_risks_and_summaries(self):
        self.command('/buy bsc '+ADDRESS+' 10')
        configured={domain:self.route for domain in ('alpha','meme','stock')}
        missing=Path(self.tmp.name)/'missing-alpha.sqlite'
        def review(p):return c.analyze(p,{'price':8,'time':time.time()-1},[],time.time(),'5m')
        with patch('holdings_service.poll',return_value={'status':'ok'}),patch('holdings_service.time.time',return_value=NOW):
            health=s.cycle(self.db,configured,NOW,review,missing)
        first=self.db.execute('SELECT count(*) FROM outbox').fetchone()[0]
        self.assertEqual(health['positions'],1);self.assertEqual(health['fresh'],1)
        with patch('holdings_service.poll',return_value={'status':'ok'}),patch('holdings_service.time.time',return_value=NOW+301):
            s.cycle(self.db,configured,NOW+301,review,missing)
        self.assertEqual(self.db.execute('SELECT count(*) FROM outbox').fetchone()[0],first)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reviews').fetchone()[0],1)

    def test_alpha_import_excludes_reference_positions(self):
        path=Path(self.tmp.name)/'alpha.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE positions(id INTEGER,mode TEXT,closed REAL,address TEXT)')
            db.executemany('INSERT INTO positions VALUES(?,?,?,?)',[(1,'actual',None,ADDRESS),(2,'reference',None,ADDRESS),(3,'actual',NOW,ADDRESS)])
        self.assertEqual([p['id'] for p in sources.alpha_positions(path)],[1])

if __name__=='__main__':unittest.main()
