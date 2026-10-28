from __future__ import annotations
import contextlib,json,sqlite3,time
from pathlib import Path
class Store:
 def __init__(self,folder):
  self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.path=self.folder/'meme-radar.sqlite'
  with self.db() as d:d.executescript('''CREATE TABLE IF NOT EXISTS pairs(key TEXT PRIMARY KEY,observed REAL,payload TEXT);CREATE TABLE IF NOT EXISTS signals(key TEXT PRIMARY KEY,created REAL,payload TEXT);CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY,created REAL,payload TEXT,state TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,next_try REAL DEFAULT 0,sent REAL,error TEXT);''')
  from forward import initialize as initialize_forward
  with self.db() as d: initialize_forward(d)
  # candidates_archive: score>0 candidates per 15-min slot, with outcome fields
  # candidates_rejected: score=0 candidates, one row per (chain,address)
  # Both tables are exempt from cleanup() — append-only, retained ≥30 days.
  with self.db() as d:d.executescript('''
   CREATE TABLE IF NOT EXISTS candidates_archive(
    key TEXT PRIMARY KEY, chain TEXT NOT NULL, address TEXT NOT NULL, symbol TEXT,
    scanned_at REAL NOT NULL, score REAL NOT NULL, tier TEXT NOT NULL,
    passed INTEGER NOT NULL DEFAULT 1,
    liquidity REAL, volume_1h REAL, buys_1h INTEGER, sells_1h INTEGER, change_1h REAL,
    safety TEXT, safety_blocks TEXT, reasons TEXT, risk TEXT,
    wallet_hits INTEGER DEFAULT 0, kol_hits INTEGER DEFAULT 0, first_seen REAL,
    entry_price REAL, peak_price REAL, peak_multiple REAL,
    last_price REAL, last_at REAL, max_at REAL,
    mark_count INTEGER DEFAULT 0, stale INTEGER DEFAULT 0,
    payload TEXT);
   CREATE INDEX IF NOT EXISTS idx_archive_chain_addr ON candidates_archive(chain, address);
   CREATE INDEX IF NOT EXISTS idx_archive_scanned ON candidates_archive(scanned_at);
   CREATE TABLE IF NOT EXISTS candidates_rejected(
    key TEXT PRIMARY KEY, chain TEXT NOT NULL, address TEXT NOT NULL, symbol TEXT,
    first_seen REAL NOT NULL, last_seen REAL, seen_count INTEGER DEFAULT 1,
    liquidity REAL, volume_1h REAL, safety TEXT, safety_blocks TEXT,
    score_reasons TEXT, entry_price REAL,
    peak_price REAL, peak_multiple REAL,
    last_price REAL, last_at REAL, stale INTEGER DEFAULT 0);
   CREATE INDEX IF NOT EXISTS idx_rejected_chain ON candidates_rejected(chain);
  ''')
 @contextlib.contextmanager
 def db(self):
  d=sqlite3.connect(self.path,timeout=15);d.row_factory=sqlite3.Row
  try:
   with d:yield d
  finally:d.close()
 def put(self,k,v):
  with self.db() as d:d.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',(k,json.dumps(v,ensure_ascii=False)))
 def state(self,k,default=None):
  with self.db() as d:r=d.execute('SELECT value FROM runtime WHERE key=?',(k,)).fetchone()
  return json.loads(r[0]) if r else default
 def enqueue(self,k,payload,now=None):
  with self.db() as d:d.execute('INSERT OR IGNORE INTO outbox(key,created,payload) VALUES(?,?,?)',(k,time.time() if now is None else now,json.dumps(payload,ensure_ascii=False)))

 def cleanup(self,now=None):
  now=time.time() if now is None else now
  with self.db() as d:
   d.execute("UPDATE outbox SET state='expired' WHERE state='pending' AND (created<? OR json_extract(payload,'$.channel') IS NULL)",(now-900,))
   d.execute('DELETE FROM pairs WHERE observed<?',(now-7*86400,))
   # Candidate snapshots roll off after 90 days; resolved forward tracks after 30 days.
   d.execute('DELETE FROM signals WHERE created<?',(now-7*86400,))
   d.execute("DELETE FROM forward_tracks WHERE created<? AND status!='open'",(now-7*86400,))
   d.execute("DELETE FROM outbox WHERE created<? AND state!='pending'",(now-7*86400,))
   # candidates_archive and candidates_rejected are NOT cleaned up here.
   # They are append-only, retained ≥30 days, exempt from rolling deletion.
