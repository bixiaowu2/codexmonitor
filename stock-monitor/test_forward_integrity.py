import json,sqlite3,unittest
from forward import initialize,register,observe,summary,HORIZONS,GRACE_SECONDS

class TestForwardIntegrity(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
  self.now=1000000;initialize(self.db)
 def row(self):return self.db.execute('SELECT * FROM forward_tracks').fetchone()
 def add(self):register(self.db,'track','asset','SYMBOL','market',10,{},self.now)
 def test_late_quote_not_backfilled_into_all_prior_horizons(self):
  self.add();observe(self.db,'asset',100,self.now+86400)
  marks=json.loads(self.row()['marks'])
  self.assertTrue(marks['1h']['missing']);self.assertTrue(marks['6h']['missing'])
  self.assertNotIn('return',marks['1h']);self.assertEqual(marks['24h']['return'],9)
 def test_absent_assets_mature_into_explicit_missing(self):
  self.add();stats=summary(self.db,self.now+604800+GRACE_SECONDS+1)
  self.assertEqual(stats['complete'],1);self.assertEqual(stats['missing_marks'],4)
  self.assertEqual(stats['marks'],0)
 def test_preexisting_bad_mark_quarantined_original_preserved(self):
  self.add();bad={'return':99,'observed_at':self.now+86400}
  self.db.execute('UPDATE forward_tracks SET marks=?',(json.dumps({'1h':bad}),))
  stats=summary(self.db,self.now+86400)
  mark=json.loads(self.row()['marks'])['1h']
  self.assertTrue(mark['missing']);self.assertEqual(mark['excluded_observation'],bad)
  self.assertEqual(stats['horizons']['1h']['valid'],0)
 def test_true_peak_to_trough_drawdown_not_entry_loss(self):
  self.add();observe(self.db,'asset',20,self.now+600);observe(self.db,'asset',15,self.now+3600)
  mark=json.loads(self.row()['marks'])['1h']
  self.assertAlmostEqual(mark['max_drawdown'],.25)
  self.assertEqual(mark['trough_return'],0)
  self.assertEqual(mark['return'],.5)
 def test_repeated_out_of_order_or_nan_prices_ignored(self):
  self.add();observe(self.db,'asset',12,self.now+100)
  self.assertEqual(observe(self.db,'asset',50,self.now+100),0)
  self.assertEqual(observe(self.db,'asset',50,self.now+99),0)
  self.assertEqual(observe(self.db,'asset',float('nan'),self.now+200),0)
  self.assertFalse(register(self.db,'bad','asset','S','m',float('inf'),{},self.now))
  self.assertEqual(self.row()['last'],12)
 def test_endpoint_grace_boundary(self):
  self.add();observe(self.db,'asset',12,self.now+3600+GRACE_SECONDS)
  self.assertEqual(summary(self.db,self.now+6000)['horizons']['1h']['valid'],1)
 def test_legacy_schema_migration_preserves_unknown_drawdown(self):
  self.db.execute('DROP TABLE forward_tracks')
  self.db.execute('CREATE TABLE forward_tracks(key TEXT PRIMARY KEY,instrument_key TEXT,symbol TEXT,market TEXT,created REAL,entry REAL,last REAL,peak REAL,trough REAL,checked REAL,marks TEXT,payload TEXT,status TEXT)')
  self.db.execute("INSERT INTO forward_tracks VALUES('old','asset','S','m',?,10,12,20,10,?,'{}','{}','open')",(self.now,self.now))
  initialize(self.db);observe(self.db,'asset',15,self.now+3600)
  self.assertIsNone(json.loads(self.row()['marks'])['1h']['max_drawdown'])
 def test_complete_legacy_rows_are_also_audited(self):
  self.add();marks={label:{'return':99,'observed_at':self.now+604800} for _,label in HORIZONS}
  self.db.execute("UPDATE forward_tracks SET status='complete',marks=?",(json.dumps(marks),))
  stats=summary(self.db,self.now+604800)
  self.assertEqual(stats['marks'],1);self.assertEqual(stats['missing_marks'],3)

if __name__=='__main__':unittest.main()
