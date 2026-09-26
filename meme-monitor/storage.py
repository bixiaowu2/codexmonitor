from __future__ import annotations
import contextlib,json,sqlite3,time
from pathlib import Path
class Store:
 def __init__(self,folder):
  self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.path=self.folder/'meme-radar.sqlite'
  with self.db() as d:d.executescript('''CREATE TABLE IF NOT EXISTS pairs(key TEXT PRIMARY KEY,observed REAL,payload TEXT);CREATE TABLE IF NOT EXISTS signals(key TEXT PRIMARY KEY,created REAL,payload TEXT);CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY,created REAL,payload TEXT,state TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,next_try REAL DEFAULT 0,sent REAL,error TEXT);''')
  from forward import initialize as initialize_forward
  with self.db() as d: initialize_forward(d)
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
   d.execute('DELETE FROM signals WHERE created<?',(now-90*86400,))
   d.execute("DELETE FROM outbox WHERE created<? AND state!='pending'",(now-7*86400,))
