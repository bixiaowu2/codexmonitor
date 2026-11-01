"""Forward measurements with explicit endpoint gaps, not future-price backfills."""
from __future__ import annotations
import json, math, time

HORIZONS=((3600,'1h'),(21600,'6h'),(86400,'24h'),(604800,'7d'))
GRACE_SECONDS=1800
# 2026-10-31 lead 1328/2dfc 裁定：6h/24h/7d 统一 grace=72h，1h 不动。
# 根因：墙钟到期点常落在休市段，30 分钟窗口内没有报价 -> 判 no_quote_in_window。
# 实测各档缺失 mark「等到下次开市」的等待：6h 中位 14.4h / 最大 65.8h，
#   24h 中位 42.4h / 最大 66.4h，7d 中位 18.4h / 最大 66.4h。
# 72h=259200s 覆盖实测最大 66.4h（长周末）并留 5.6h 余量。
# 注意：grace 是【允许迟到取价】，不是延长视界 —— 到期点仍按墙钟算。
# 口径：延迟必须按 on_time/overnight/weekend 分层报（见 DELAY_BUCKETS），
#       只有 on_time(<=30min) 可引用为「墙钟 N 小时收益」。
GRACE_OVERRIDE={'6h':259200,'24h':259200,'7d':259200}
def _grace(label):
    return GRACE_OVERRIDE.get(label,GRACE_SECONDS)
# 2026-10-31 lead 1328/2dfc 条件⑤：延迟分层。
# 只有 on-time 层可以引用为「墙钟 N 小时收益」；
# overnight / weekend 是延迟补取观察，不得冒充墙钟收益。
DELAY_BUCKETS=(('on_time',0,1800),('overnight',1800,86400),('weekend',86400,259200))
def _delay_of(mark):
    d=mark.get('delay_seconds')
    if d is None:
        obs=mark.get('observed_at');due=mark.get('due_at')
        if valid_number(obs) and valid_number(due): d=obs-due
    return d
def _bucket_of(mark):
    d=_delay_of(mark)
    if d is None: return None
    for name,lo,hi in DELAY_BUCKETS:
        if lo<=d<=hi: return name
    return 'over_grace'
LEDGER_VERSION='forward-v2'


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS forward_tracks(
        key TEXT PRIMARY KEY, instrument_key TEXT NOT NULL, symbol TEXT NOT NULL,
        market TEXT NOT NULL, created REAL NOT NULL, entry REAL NOT NULL,
        last REAL NOT NULL, peak REAL NOT NULL, trough REAL NOT NULL,
        checked REAL NOT NULL, marks TEXT NOT NULL DEFAULT '{}', payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open', max_drawdown REAL)''')
    if 'max_drawdown' not in {r[1] for r in db.execute('PRAGMA table_info(forward_tracks)')}:
        # NULL preserves that the full legacy path drawdown cannot be recovered.
        db.execute('ALTER TABLE forward_tracks ADD COLUMN max_drawdown REAL')


def valid_number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>0


def register(db,key,instrument_key,symbol,market,entry,payload,now=None):
    now=time.time() if now is None else now
    if not valid_number(entry) or not valid_number(now):return False
    initialize(db)
    saved=dict(payload,ledger_version=LEDGER_VERSION)
    db.execute('''INSERT OR IGNORE INTO forward_tracks
      (key,instrument_key,symbol,market,created,entry,last,peak,trough,checked,marks,payload,status,max_drawdown)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(key,instrument_key,symbol,market,now,entry,entry,entry,entry,now,'{}',json.dumps(saved,ensure_ascii=False),'open',0.0))
    return True


def normalize_marks(row,now):
    marks=json.loads(row['marks'] or '{}')
    for seconds,label in HORIZONS:
        due=row['created']+seconds
        mark=marks.get(label)
        if mark and 'return' in mark:
            observed=mark.get('observed_at')
            if not valid_number(observed) or not due<=observed<=due+_grace(label):
                marks[label]={'missing':True,'reason':'observation_outside_window','due_at':due,
                              'excluded_observation':mark}
        if label not in marks and now>due+_grace(label):
            marks[label]={'missing':True,'reason':'no_quote_in_window','due_at':due}
    return marks


def observe(db,instrument_key,price,now=None):
    now=time.time() if now is None else now
    if not valid_number(price) or not valid_number(now):return 0
    initialize(db)
    rows=db.execute("SELECT * FROM forward_tracks WHERE instrument_key=? AND status='open'",(instrument_key,)).fetchall()
    updated=0
    for row in rows:
        if now<=row['checked']:continue  # No repeated or out-of-order quotes.
        marks=normalize_marks(row,now)
        peak=max(float(row['peak']),price);trough=min(float(row['trough']),price)
        drawdown=max(row['max_drawdown'],1-price/peak) if row['max_drawdown'] is not None else None
        for seconds,label in HORIZONS:
            due=row['created']+seconds
            if due<=now<=due+_grace(label) and label not in marks:
                marks[label]={'return':price/row['entry']-1,'peak_multiple':peak/row['entry'],
                              'trough_return':trough/row['entry']-1,'max_drawdown':drawdown,
                              'observed_at':now,'due_at':due,'delay_seconds':now-due,'grace_seconds':_grace(label),
                              'ledger_version':LEDGER_VERSION}
        status='complete' if all(label in marks for _,label in HORIZONS) else 'open'
        db.execute('UPDATE forward_tracks SET last=?,peak=?,trough=?,max_drawdown=?,checked=?,marks=?,status=? WHERE key=?',
                   (price,peak,trough,drawdown,now,json.dumps(marks,ensure_ascii=False),status,row['key']))
        updated+=1
    return updated


def summary(db,now=None):
    now=time.time() if now is None else now
    initialize(db)
    rows=db.execute('SELECT * FROM forward_tracks').fetchall()
    horizons={label:{'due':0,'valid':0,'missing':0,'pending':0,'on_time':0,'overnight':0,'weekend':0,'over_grace':0,'no_delay':0} for _,label in HORIZONS}
    complete=valid_marks=missing_marks=0
    for row in rows:
        marks=normalize_marks(row,now)
        done=all(label in marks for _,label in HORIZONS)
        complete+=done
        status='complete' if done else 'open'
        if marks!=json.loads(row['marks']) or row['status']!=status:
            db.execute('UPDATE forward_tracks SET marks=?,status=? WHERE key=?',(json.dumps(marks,ensure_ascii=False),status,row['key']))
        for seconds,label in HORIZONS:
            h=horizons[label];mark=marks.get(label,{})
            due=now>=row['created']+seconds
            valid='return' in mark
            missing=bool(mark.get('missing'))
            h['due']+=due;h['valid']+=valid;h['missing']+=missing;h['pending']+=due and not (valid or missing)
            if valid:
                _b=_bucket_of(mark)
                h[_b if _b in ('on_time','overnight','weekend','over_grace') else 'no_delay']+=1
            valid_marks+=valid;missing_marks+=missing
    return {'ledger_version':LEDGER_VERSION,'tracks':len(rows),'open':len(rows)-complete,
            'complete':complete,'marks':valid_marks,'missing_marks':missing_marks,'horizons':horizons,
            'grace_seconds':GRACE_SECONDS,'lead_time':None,
            'notice':'Wall-clock horizons; complete means resolved, including missing endpoints. Peak/trough are sampled, not realized profits. Legacy full-path drawdown and lead time unavailable. Valid marks are stratified by delay: only on_time (<=30min) may be cited as wall-clock N-hour return; overnight (30min-24h) and weekend (24h-72h) are delayed backfill observations, not wall-clock returns.'}
