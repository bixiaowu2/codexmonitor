from __future__ import annotations
import json,time

HORIZONS=((3600,'1h'),(21600,'6h'),(86400,'24h'),(604800,'7d'))

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS forward_tracks(
        key TEXT PRIMARY KEY, instrument_key TEXT NOT NULL, symbol TEXT NOT NULL,
        market TEXT NOT NULL, created REAL NOT NULL, entry REAL NOT NULL,
        last REAL NOT NULL, peak REAL NOT NULL, trough REAL NOT NULL,
        checked REAL NOT NULL, marks TEXT NOT NULL DEFAULT '{}', payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open')''')

def register(db,key,instrument_key,symbol,market,entry,payload,now=None):
    now=time.time() if now is None else now
    if not isinstance(entry,(int,float)) or entry<=0:return False
    initialize(db)
    db.execute('''INSERT OR IGNORE INTO forward_tracks
      (key,instrument_key,symbol,market,created,entry,last,peak,trough,checked,marks,payload,status)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(key,instrument_key,symbol,market,now,entry,entry,entry,entry,now,'{}',json.dumps(payload,ensure_ascii=False),'open'))
    return True

def observe(db,instrument_key,price,now=None):
    now=time.time() if now is None else now
    if not isinstance(price,(int,float)) or price<=0:return 0
    initialize(db);rows=db.execute("SELECT * FROM forward_tracks WHERE instrument_key=? AND status='open'",(instrument_key,)).fetchall();updated=0
    for row in rows:
        marks=json.loads(row['marks'] or '{}');peak=max(float(row['peak']),price);trough=min(float(row['trough']),price);age=now-float(row['created'])
        for seconds,label in HORIZONS:
            if age>=seconds and label not in marks:
                marks[label]={'return':price/row['entry']-1,'peak_multiple':peak/row['entry'],'trough_return':trough/row['entry']-1,'observed_at':now}
        status='complete' if all(label in marks for _,label in HORIZONS) else 'open'
        db.execute('UPDATE forward_tracks SET last=?,peak=?,trough=?,checked=?,marks=?,status=? WHERE key=?',(price,peak,trough,now,json.dumps(marks,ensure_ascii=False),status,row['key']));updated+=1
    return updated

def summary(db):
    initialize(db);rows=db.execute('SELECT status,marks FROM forward_tracks').fetchall();marks=[]
    for row in rows:
        for label,data in json.loads(row['marks'] or '{}').items():marks.append({'horizon':label,**data})
    return {'tracks':len(rows),'open':sum(r['status']=='open' for r in rows),'complete':sum(r['status']=='complete' for r in rows),'marks':len(marks)}
