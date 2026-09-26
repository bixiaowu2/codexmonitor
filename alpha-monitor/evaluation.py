"""Prospective outcomes only. Never optimize thresholds on the same observed winners."""
import collections,hashlib,json,time
from pathlib import Path
from radar import dump,utc
from positions import emit,VERSION

HORIZONS=(24,72,168,720,2160,4320)

def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS evaluation_tracks(
      key TEXT PRIMARY KEY,address TEXT,symbol TEXT,role TEXT,stage TEXT,version TEXT,opened REAL,
      entry REAL,last REAL,peak REAL,trough REAL,max_drawdown REAL DEFAULT 0,
      observed REAL,quote_time REAL,samples INTEGER DEFAULT 1,max_gap REAL DEFAULT 0,
      suspect_jump INTEGER DEFAULT 0,horizons TEXT DEFAULT '{}',features TEXT);
    CREATE INDEX IF NOT EXISTS evaluation_address ON evaluation_tracks(address,opened);
    ''')

def register(db,key,address,symbol,quote,stage,features,now,role='signal'):
    db.execute('INSERT OR IGNORE INTO evaluation_tracks(key,address,symbol,role,stage,version,opened,entry,last,peak,trough,observed,quote_time,features) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (key,address,symbol,role,stage,VERSION,now,quote['price'],quote['price'],quote['price'],quote['price'],now,quote['time'],json.dumps(features)))

def observe(db,address,quote,now):
    if now*1000-quote['time']>180000:return
    for t in db.execute('SELECT * FROM evaluation_tracks WHERE address=? AND opened>?',(address,now-181*86400)).fetchall():
        if quote['time']<=t['quote_time']:continue
        price=quote['price'];peak=max(price,t['peak']);marks=json.loads(t['horizons']);age=now-t['opened']
        for h in HORIZONS:
            if age>=h*3600 and str(h) not in marks:
                marks[str(h)]={'price_multiple':price/t['entry'],'observed':now} if age-h*3600<=1800 else {'missing':True}
        jump=t['suspect_jump'] or max(price/t['last'],t['last']/price)>10
        db.execute('UPDATE evaluation_tracks SET last=?,peak=?,trough=?,max_drawdown=?,observed=?,quote_time=?,samples=samples+1,max_gap=?,suspect_jump=?,horizons=? WHERE key=?',
                   (price,peak,min(price,t['trough']),max(t['max_drawdown'],1-price/peak),now,quote['time'],max(t['max_gap'],now-t['observed']),int(jump),json.dumps(marks),t['key']))

def controls(db,observations,tokens,now):
    """Eight deterministic random controls/day from freshly observed eligible assets, not chosen by future gains."""
    day=int(now//86400)
    row=db.execute('SELECT value FROM runtime WHERE key=?',('controls_day',)).fetchone()
    if row and json.loads(row[0])==day:return
    eligible=[o for o in observations if not o['blocks'] and now*1000-o['quote']['time']<=180000]
    if not eligible:return
    chosen=sorted(eligible,key=lambda o:hashlib.sha256((str(day)+o['address']).encode()).hexdigest())[:8]
    for o in chosen:
        register(db,f'control:{day}:{o["address"]}',o['address'],o['symbol'],o['quote'],'daily_control',{'market':o['market'],'features':o['features']},now,'control')
    db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',('controls_day',json.dumps(day)))

def report(store,now=None,force=False):
    now=time.time() if now is None else now;day=int(now//86400)
    if not force and store.state('evaluation_report_day')==day:return
    with store.db() as db:
        initialize(db)
        for t in db.execute('SELECT key,opened,horizons FROM evaluation_tracks').fetchall():
            marks=json.loads(t['horizons'])
            for h in HORIZONS:
                if now-t['opened']>h*3600+1800 and str(h) not in marks:marks[str(h)]={'missing':True}
            db.execute('UPDATE evaluation_tracks SET horizons=? WHERE key=?',(json.dumps(marks),t['key']))
        tracks=[dict(t) for t in db.execute('SELECT * FROM evaluation_tracks')]
        position_counts={r[0]:r[1] for r in db.execute('SELECT mode,count(*) FROM positions GROUP BY mode')}
    grouped=collections.defaultdict(list)
    for t in tracks:grouped[(t['version'],t['role'],t['stage'])].append(t)
    rows=[]
    for (version,role,stage),items in grouped.items():
        mature=[t for t in items if now-t['opened']>=90*86400]
        observed=[t for t in mature if 'price_multiple' in json.loads(t['horizons']).get('2160',{}) and not t['suspect_jump']]
        rows.append({'version':version,'role':role,'stage':stage,'tracks':len(items),'mature_90d':len(mature),'usable_90d':len(observed),
                     'observed_5x':sum(t['peak']/t['entry']>=5 for t in items),'observed_10x':sum(t['peak']/t['entry']>=10 for t in items),'observed_100x':sum(t['peak']/t['entry']>=100 for t in items),
                     'suspect_jump_tracks':sum(bool(t['suspect_jump']) for t in items),'tracks_with_gap_over_hour':sum(t['max_gap']>3600 for t in items)})
    result={'as_of':utc(now*1000),'rule_version':VERSION,'tracks':len(tracks),'groups':rows,'positions_by_mode':position_counts,
            'optimization_status':'collecting_prospective_evidence_no_automatic_threshold_change',
            'method':'Signal and deterministic daily control cohorts; same token can appear in both, not independent trials; sampled quote paths, no orders/fees/slippage.',
            'limitations':['100x observed maximum is not realized profit or an estimated success probability','Controls cover freshly observed eligible assets, not a complete historical universe or all missed pumps','Changes need chronological holdout, asset-level grouping, sufficient mature cases and versioned forward testing; no blind winner-based tuning']}
    dump(store.folder/'evaluation-report.json',result)
    lines=['# 前瞻效果复盘',f'UTC {result["as_of"]}',f'当前记录 {len(tracks)} 条；仅积累证据，未自动修改策略阈值。',
           '|版本|类别|阶段|记录|满90天|90天端点可用|采样触及5倍|10倍|100倍|异常跳价|超过1小时采样空档|','|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:lines.append('|'+ '|'.join(str(r[k]) for k in ('version','role','stage','tracks','mature_90d','usable_90d','observed_5x','observed_10x','observed_100x','suspect_jump_tracks','tracks_with_gap_over_hour'))+'|')
    lines+=['实际持仓和假定买入参考仓分开记录。峰值倍数不是可实现收益；未扣费用、滑点或模拟完整退出。',
            '同币多信号不是独立样本。参数修改应按时间留出后续样本，并按资产分组，避免训练/验证泄漏。只有赢家不能验证规律；0个成熟样本也不代表0%成功率。',
            '不追溯填入上线前最低价，不用当前市值伪造历史特征。对照仅来自实际检查到的新鲜可用报价，不代表完整漏检率。']
    (store.folder/'效果复盘.md').write_text('\n\n'.join(lines),encoding='utf-8')
    store.put('evaluation_report_day',day)
    # Weekly report is useful even when market signals are quiet; no daily status spam.
    week=int(now//(7*86400))
    if store.state('evaluation_notified_week')!=week:
        store.enqueue(f'evaluation-week:{week}',{'kind':'ops','text':f'📊 Alpha雷达前瞻复盘：已记录 {len(tracks)} 条信号/对照路径。\n策略版本 {VERSION}；目前仍在积累样本，不把采样高点当收益，不据短期赢家自动调参。\nTelegram /performance 查看概况。'},now=now)
        store.put('evaluation_notified_week',week)
    return result
