import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from inputs import shared_x_mentions,enrich
from scoring import score

class SocialTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'feed.sqlite';self.now=time.time();self.addr='0x'+'a'*40
  with sqlite3.connect(self.path) as d:
   d.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,payload TEXT); CREATE TABLE accounts(account TEXT,last_success REAL,failures INTEGER); CREATE TABLE posts(id TEXT,account TEXT,url TEXT,text TEXT,published REAL,observed REAL);')
   d.execute('INSERT INTO metadata VALUES(?,?)',('accounts',json.dumps({'lookonchain':{'category':'research','interval_seconds':120}})))
   d.execute('INSERT INTO accounts VALUES(?,?,?)',('lookonchain',self.now,0))
 def add(self,text,url='https://x.com/lookonchain/status/123',age=30):
  with sqlite3.connect(self.path) as d:d.execute('INSERT INTO posts VALUES(?,?,?,?,?,?)',('123','lookonchain',url,text,self.now-age,self.now))
 def pairs(self):return [{'chain':'bsc','address':self.addr},{'chain':'arc','address':self.addr}]
 def test_exact_chain_address_match(self):
  self.add('Scam warning on BSC '+self.addr)
  e,h=shared_x_mentions(self.path,self.pairs(),self.now)
  self.assertEqual(len(e),1);self.assertEqual(e[0]['chain'],'bsc');self.assertEqual(e[0]['stance'],'unclassified');self.assertEqual(h['status'],'ok')
 def test_no_chain_no_evm_attribution(self):
  self.add(self.addr);self.assertEqual(shared_x_mentions(self.path,self.pairs(),self.now)[0],[])
 def test_multiple_chains_ambiguous(self):
  self.add('BSC and on Arc '+self.addr);self.assertEqual(shared_x_mentions(self.path,self.pairs(),self.now)[0],[])
 def test_wrong_author_rejected(self):
  self.add('BSC '+self.addr,url='https://x.com/other/status/123');self.assertEqual(shared_x_mentions(self.path,self.pairs(),self.now)[0],[])
 def test_stale_post_ignored(self):
  self.add('BSC '+self.addr,age=90000);self.assertEqual(shared_x_mentions(self.path,self.pairs(),self.now)[0],[])
 def test_solana_case_sensitive(self):
  addr='AbC'*11;self.add(addr)
  pairs=[{'chain':'solana','address':addr},{'chain':'solana','address':addr.lower()}]
  e,_=shared_x_mentions(self.path,pairs,self.now);self.assertEqual([x['address'] for x in e],[addr])
 def test_social_does_not_turn_risk_mention_into_bonus(self):
  self.add('Scam warning on BSC '+self.addr);pairs=self.pairs();events,_=shared_x_mentions(self.path,pairs,self.now)
  p=pairs[0];p.update(liquidity_usd=100000,volume_1h=10000,buys_1h=5,sells_1h=5)
  before=score(p)['score'];enrich([p],{},[],events)
  self.assertEqual(p['x_account_count'],1);self.assertEqual(score(p)['score'],before)
 def test_missing_db_explicit(self):
  self.assertEqual(shared_x_mentions(Path(self.tmp.name)/'missing',[],self.now)[1]['status'],'unavailable')

if __name__=='__main__':unittest.main()
