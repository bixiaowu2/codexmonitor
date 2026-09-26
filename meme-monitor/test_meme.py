import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from config import Config
from scoring import score,age_minutes
from sources import NETWORK,Collector,normalize_pair,parse_gecko,profile_addresses
from net import FetchError
from storage import Store
from inputs import enrich,kol_events
from radar import cycle
from cloud import deliver_one
from notify import send_channel

class TestMeme(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.cfg=Config.load({'MEME_DATA':self.tmp.name});self.store=Store(self.tmp.name)
 def pool(self,**kw):
  p=normalize_pair({'baseToken':{'address':'0xAB','symbol':'TEST'},'pairAddress':'0xCD','pairCreatedAt':time.time()-60,'priceUsd':'1','liquidity':{'usd':50000},'volume':{'h1':50000},'txns':{'h1':{'buys':80,'sells':20}},'priceChange':{'h1':10}},'bsc','test');p.update(kw);return p
 def test_profile_budget_after_chain_filter(self):
  rows=[{'chainId':'solana','tokenAddress':'A'*32}]*20+[{'chainId':'bsc','tokenAddress':'0x'+'a'*40}]
  self.assertEqual(profile_addresses(rows,'bsc'),['0x'+'a'*40])
 def test_profile_url_injection_rejected(self):
  self.assertEqual(profile_addresses([{'chainId':'bsc','tokenAddress':'evil?url=x'}],'bsc'),[])
 def test_profile_solana_case_preserved(self):
  self.assertEqual(profile_addresses([{'chainId':'solana','tokenAddress':'AbC'*11}],'solana'),['AbC'*11])
 def test_all_six_networks(self):self.assertEqual(set(self.cfg.chains),set(NETWORK))
 def test_solana_case(self):
  p=normalize_pair({'baseToken':{'address':'AbCDef'},'pairAddress':'PoOL'},'solana','test');self.assertEqual(p['address'],'AbCDef');self.assertEqual(p['pair_address'],'PoOL')
 def test_evm_case(self):self.assertEqual(self.pool()['address'],'0xab')
 def test_missing_is_unknown(self):self.assertIsNone(normalize_pair({},'bsc','test')['buys_1h'])
 def test_gecko_live_fixtures(self):
  for chain in ('arc','stable','x-layer','robinhood'):
   with self.subTest(chain=chain):
    data=json.loads((Path(__file__).parent/'fixtures'/(chain+'.json')).read_text());p=parse_gecko(data,'xlayer' if chain=='x-layer' else chain)[0];a=data['data'][0]['attributes']
    self.assertEqual(p['volume_1h'],float(a['volume_usd']['h1']));self.assertIsNotNone(p['created_at']);self.assertTrue(p['dex']);self.assertTrue(p['address'])
 def test_gecko_invalid_schema(self):
  with self.assertRaises(FetchError):parse_gecko({'error':'bad'},'arc')
 def test_gecko_solana_case(self):
  data={'data':[{'id':'solana_PoOL','attributes':{},'relationships':{'base_token':{'data':{'id':'solana_AbC'}}}}]}
  self.assertEqual(parse_gecko(data,'solana')[0]['address'],'AbC')
 def test_http_failure_empty_and_backoff(self):
  c=Collector(spacing=0)
  with patch('sources.get_json',side_effect=[FetchError('http_429'),{'data':[]}]):
   p,s=c.collect(['arc']);self.assertEqual([x['status'] for x in s],['failed','empty']);self.assertEqual(s[0]['error'],'http_429')
  with patch('sources.get_json',return_value={'data':[]}) as get:
   p,s=c.collect(['arc']);self.assertEqual(s[0]['status'],'backoff');self.assertEqual(get.call_count,1)
 def test_low_liquidity_block(self):self.assertEqual(score(self.pool(liquidity_usd=100))['score'],0)
 def test_missing_volume_block(self):self.assertEqual(score(self.pool(volume_1h=None))['score'],0)
 def test_overextension_penalty(self):self.assertLess(score(self.pool(change_5m=50))['score'],score(self.pool(change_5m=5))['score'])
 def test_age_milliseconds(self):self.assertAlmostEqual(age_minutes({'created_at':(time.time()-60)*1000},time.time()),1,places=2)
 def test_buy_share_and_unknown_safety(self):
  s=score(self.pool());self.assertTrue(any('笔数占比' in r for r in s['reasons']));self.assertTrue(any('尚未验证' in r for r in s['risk']))
 def test_symbol_only_not_attributed(self):
  p=self.pool();enrich([p],{},[{'symbol':'TEST','chain':'bsc'}],[]);self.assertEqual(p['kol_hits'],0)
 def test_cross_chain_not_attributed(self):
  p=self.pool();enrich([p],{},[{'address':p['address'],'chain':'arc'}],[]);self.assertEqual(p['kol_hits'],0)
 def test_events_fresh_dedup(self):
  path=Path(self.tmp.name)/'events';e={'address':'AbC','chain':'solana','url':'https://x.com/a/status/1','observed_at':time.time()}
  path.write_text('\n'.join(json.dumps(x) for x in [e,e,dict(e,observed_at=0),dict(e,observed_at=time.time()+900)]));self.assertEqual(len(kol_events(path)),1)
 def test_representative_and_disabled_no_queue(self):
  c=Collector(spacing=0)
  with patch.object(c,'collect',return_value=([self.pool(),self.pool(pair_address='0xother',liquidity_usd=10000)],[])):
   report=cycle(self.cfg,self.store,c)
  self.assertEqual(report['candidates'],1);self.assertEqual(report['rows'][0]['pair_address'],'0xcd')
  with self.store.db() as d:self.assertEqual(d.execute('SELECT count(*) FROM outbox').fetchone()[0],0);self.assertEqual(d.execute('SELECT count(*) FROM signals').fetchone()[0],1)
 def test_hot_event_is_queued_once_per_cooldown(self):
  from dataclasses import replace
  cfg=replace(self.cfg,telegram_enabled=True,dingtalk_enabled=False,telegram_token='1:x',telegram_chat='1')
  c=Collector(spacing=0);p=self.pool(liquidity_usd=100000,volume_1h=250000,change_1h=30)
  with patch.object(c,'collect',return_value=([p],[])):
   first=cycle(cfg,self.store,c);second=cycle(cfg,self.store,c)
  self.assertEqual(first['events'][0]['kind'],'new_hot')
  self.assertEqual(second['events'],[])
  with self.store.db() as d:self.assertEqual(d.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0],2)
 def test_stale_and_legacy_queue_expired(self):
  self.store.enqueue('old',{'channel':'telegram','text':'x'},time.time()-901);self.store.enqueue('legacy',{'text':'x'});self.store.cleanup()
  with self.store.db() as d:self.assertEqual(d.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0],0)
 def test_telegram_api_reject(self):
  from dataclasses import replace
  cfg=replace(self.cfg,telegram_enabled=True)
  with patch('notify.post_json',return_value={'ok':False}):
   with self.assertRaises(RuntimeError):send_channel(cfg,'test','telegram')
 def test_channels_retry_independently(self):
  from dataclasses import replace
  cfg=replace(self.cfg,telegram_enabled=True,dingtalk_enabled=True)
  for channel in ('telegram','dingtalk'):self.store.enqueue(channel,{'channel':channel,'text':'x'})
  with patch('cloud.send_channel',side_effect=[None,RuntimeError('reject')]) as send:
   deliver_one(cfg,self.store);deliver_one(cfg,self.store);self.assertEqual(send.call_count,2)
  with self.store.db() as d:states={r['key']:r['state'] for r in d.execute('SELECT * FROM outbox')}
  self.assertEqual(states,{'telegram':'sent','dingtalk':'pending'})

if __name__=='__main__':unittest.main()
