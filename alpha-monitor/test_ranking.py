import json,tempfile,unittest
import cloud,dingtalk,ranking,positions
NOW=1800000000.
def obs(symbol='AAA',**kw):
 d={'symbol':symbol,'address':'0x'+format(sum(map(ord,symbol)),'040x'),'blocks':[],'quote':{'price':10.1,'time':NOW*1000},'features':{'end':int(NOW*1000)//3600000*3600000-1,'level':10,'close':10.1,'quote_volume':20000,'volume_ratio':3,'return_1h':.03,'return_24h':.2},'dual_matched':True,'token':{'liquidity':200000,'marketCap':1e7,'fdv':2e7},'safety':{}}
 d.update(kw);return d
class Ranking(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.store=cloud.Store(self.tmp.name);dingtalk.initialize(self.store)
 def tearDown(self):self.tmp.cleanup()
 def test_excludes_stale_future_risky_or_missing_data(self):
  samples=[obs('OK'),obs('OLD',quote={'price':10,'time':(NOW-181)*1000}),obs('FUTURE',quote={'price':10,'time':(NOW+1)*1000}),obs('BLOCK',blocks=['risk']),obs('RISK',safety={'flags':['mint']}),obs('MISSING',token={}),obs('FEATURE',features=None)]
  self.assertEqual([r['symbol'] for r in ranking.rank(samples,NOW)],['OK'])
 def test_requires_last_closed_hour_and_not_overextended(self):
  o=obs();o['features']['end']-=3600000
  self.assertEqual(ranking.rank([o,obs('CHASE',quote={'price':15,'time':NOW*1000})],NOW),[])
 def test_dual_first_bounded_score_and_volume_order(self):
  a=obs('A');b=obs('B');b['features']['volume_ratio']=5;secondary=obs('C',dual_matched=False);secondary['features']['volume_ratio']=6
  rows=ranking.rank([a,b,secondary],NOW);self.assertEqual([r['symbol'] for r in rows],['B','A','C'])
  for row in rows:self.assertTrue(0<=row['score']<=100)
 def test_unknown_safety_not_presented_as_no_risk(self):
  text=ranking.format_message(ranking.rank([obs()],NOW),NOW);self.assertIn('安全数据未知/未检查',text);self.assertIn('解锁未知',text);self.assertIn('不是百倍概率',text)
 def test_expired_setup_not_current_entry(self):
  row=ranking.rank([obs(setup=1,setup_seen=NOW-90000)],NOW)[0];self.assertIn('尚无有效入场参考',row['stage'])
 def test_periodic_dedup_persists_and_both_channels_render(self):
  report={'observations':[obs()],'checked':1};self.assertTrue(ranking.maybe_emit(self.store,report,NOW,21600));self.assertFalse(ranking.maybe_emit(cloud.Store(self.tmp.name),report,NOW+60,21600))
  c=cloud.Config.load({'TELEGRAM_ENABLED':'true','TELEGRAM_BOT_TOKEN':'123:FAKE','TELEGRAM_CHAT_ID':'123','DINGTALK_ENABLED':'true','DINGTALK_WEBHOOK':'https://oapi.dingtalk.com/robot/send?access_token=FAKE'})
  tg=[];ding=[];cloud.deliver_one(self.store,c,send=lambda c,t:tg.append(t),now=NOW);dingtalk.deliver_one(self.store,c,send_fn=lambda c,t:ding.append(t),now=NOW)
  self.assertEqual(len(tg),1);self.assertEqual(len(ding),1);self.assertIn('AAA',tg[0]);self.assertIn('AAA',ding[0])
 def test_empty_partial_report_explains_coverage(self):
  ranking.maybe_emit(self.store,{},NOW);self.assertIn('数据异常1项',self.store.state('ranking_latest')['text'])
 def test_interval_bounds(self):
  self.assertEqual(ranking.interval_seconds({'RANKING_INTERVAL_SECONDS':'3600'}),3600)
  with self.assertRaises(ValueError):ranking.interval_seconds({'RANKING_INTERVAL_SECONDS':'300'})
 def test_rollback_keeps_outbox_and_bookmark_atomic(self):
  with self.store.db() as db:db.execute("CREATE TRIGGER fail_state BEFORE INSERT ON runtime BEGIN SELECT RAISE(ABORT,'test'); END")
  with self.assertRaises(Exception):ranking.maybe_emit(self.store,{'observations':[obs()]},NOW)
  with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
 def test_ranking_command_is_explicit_archive(self):
  ranking.maybe_emit(self.store,{'observations':[obs()]},NOW)
  with self.store.db() as db:text=positions.handle_command(db,'/ranking',self.tmp.name,'test',NOW)
  self.assertIn('不是实时行情',text)
if __name__=='__main__':unittest.main()
