"""Forward measurements with explicit endpoint gaps, not future-price backfills."""
from __future__ import annotations
import json, math, time

HORIZONS=((3600,'1h'),(21600,'6h'),(86400,'24h'),(604800,'7d'))
GRACE_SECONDS=1800
LEDGER_VERSION='forward-v2'
# 入场价合理性护栏。依据实测分布：
#   真实代币价 p1≈4.7e-12、p50≈2.5e-05 —— 低于 1e-15 不可能是真价
#   真实最大涨幅 3893x —— 观测价超过入场价 1e4 倍，只可能是入场价登记错了
# 只设上限不设下限：跌破入场价一万倍是真实归零，属有效数据。
MIN_ENTRY_PRICE=1e-15
# 稳定币不该进 meme 雷达: 价格不动, 只会把中位数往 0 拉。
# 实测曾混入 60 条 (USDT 49 / USDC 10 / TUSD 1), 已清理。
STABLE_SYMBOLS={'USDT','USDC','TUSD','DAI','FDUSD','USDE','USDS','USDD','PYUSD','BUSD'}
MAX_PLAUSIBLE_RATIO=1e4


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
    if entry<MIN_ENTRY_PRICE:return False   # 垃圾价, 登记了也只会污染统计
    if str(symbol or '').strip().upper() in STABLE_SYMBOLS:return False
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
            if not valid_number(observed) or not due<=observed<=due+GRACE_SECONDS:
                marks[label]={'missing':True,'reason':'observation_outside_window','due_at':due,
                              'excluded_observation':mark}
        if label not in marks and now>due+GRACE_SECONDS:
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
        if price>row['entry']*MAX_PLAUSIBLE_RATIO:
            # 观测价比入场价高出一万倍 —— 不是真涨, 是入场价登记错了。
            # 冻结这条: 不再更新 peak/trough, 也不再参与统计。
            db.execute("UPDATE forward_tracks SET status='suspect', checked=? WHERE key=?",(now,row['key']))
            continue
        marks=normalize_marks(row,now)
        peak=max(float(row['peak']),price);trough=min(float(row['trough']),price)
        drawdown=max(row['max_drawdown'],1-price/peak) if row['max_drawdown'] is not None else None
        for seconds,label in HORIZONS:
            due=row['created']+seconds
            if due<=now<=due+GRACE_SECONDS and label not in marks:
                marks[label]={'return':price/row['entry']-1,'peak_multiple':peak/row['entry'],
                              'trough_return':trough/row['entry']-1,'max_drawdown':drawdown,
                              'observed_at':now,'due_at':due,'delay_seconds':now-due,
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
    horizons={label:{'due':0,'valid':0,'missing':0,'pending':0} for _,label in HORIZONS}
    complete=valid_marks=missing_marks=0
    for row in rows:
        marks=normalize_marks(row,now)
        done=all(label in marks for _,label in HORIZONS)
        complete+=done
        # suspect 是 observe() 标的'入场价不可信', 别被这里重算回 open
        status=('suspect' if row['status']=='suspect' else ('complete' if done else 'open'))
        if marks!=json.loads(row['marks']) or row['status']!=status:
            db.execute('UPDATE forward_tracks SET marks=?,status=? WHERE key=?',(json.dumps(marks,ensure_ascii=False),status,row['key']))
        for seconds,label in HORIZONS:
            h=horizons[label];mark=marks.get(label,{})
            due=now>=row['created']+seconds
            valid='return' in mark
            missing=bool(mark.get('missing'))
            h['due']+=due;h['valid']+=valid;h['missing']+=missing;h['pending']+=due and not (valid or missing)
            valid_marks+=valid;missing_marks+=missing
    return {'ledger_version':LEDGER_VERSION,'tracks':len(rows),'open':len(rows)-complete,
            'complete':complete,'marks':valid_marks,'missing_marks':missing_marks,'horizons':horizons,
            'grace_seconds':GRACE_SECONDS,'lead_time':None,
            'notice':'Wall-clock horizons; complete means resolved, including missing endpoints. Peak/trough are sampled, not realized profits. Legacy full-path drawdown and lead time unavailable.'}
