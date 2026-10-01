"""Manual spot-position journal and alerts; never submits orders."""
from __future__ import annotations
import json,re,sqlite3,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from radar import num

VERSION='layered-v3.0'

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS positions(
      id INTEGER PRIMARY KEY,request_key TEXT UNIQUE,address TEXT NOT NULL,symbol TEXT NOT NULL,
      alpha_id TEXT NOT NULL,entry REAL NOT NULL,created REAL NOT NULL,stop_pct REAL NOT NULL,
      trail_pct REAL NOT NULL,peak REAL NOT NULL,liquidity_base REAL,closed REAL,last_price REAL,last_checked REAL);
    CREATE TABLE IF NOT EXISTS position_alerts(position_id INTEGER,kind TEXT,created REAL,PRIMARY KEY(position_id,kind));
    CREATE TABLE IF NOT EXISTS position_quote_health(
      position_id INTEGER PRIMARY KEY,failed_since REAL,last_failure REAL,alert_id INTEGER,
      reason TEXT,last_quote_time REAL,source TEXT);
    CREATE TABLE IF NOT EXISTS strategy_state(address TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS strategy_events(key TEXT PRIMARY KEY,created REAL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS paper_tracks(
      key TEXT PRIMARY KEY,address TEXT,symbol TEXT,created REAL,entry REAL,last REAL,
      peak REAL,trough REAL,checked REAL,samples INTEGER DEFAULT 1,marks TEXT DEFAULT '{}');
    ''')
    columns={r[1] for r in db.execute('PRAGMA table_info(positions)')}
    if 'mode' not in columns:db.execute("ALTER TABLE positions ADD COLUMN mode TEXT NOT NULL DEFAULT 'actual'")
    from alerts import initialize as init_alerts
    init_alerts(db)

def emit(db,key,text,now):
    db.execute('INSERT OR IGNORE INTO outbox(key,created,payload) VALUES(?,?,?)',
               (key,now,json.dumps({'kind':'ops','text':text},ensure_ascii=False)))

def open_positions(db):return list(db.execute('SELECT * FROM positions WHERE closed IS NULL ORDER BY id'))

def position_tick(db,p,quote,liquidity,now):
    from alerts import raise_alert,resolve
    from quotes import valid
    if not valid(quote,now):return
    health=db.execute('SELECT * FROM position_quote_health WHERE position_id=?',(p['id'],)).fetchone()
    if health and health['last_quote_time'] is not None and quote['time']<health['last_quote_time']:return
    previous=db.execute("SELECT id FROM important_alerts WHERE base=? AND state IN ('open','acknowledged','expired') ORDER BY id DESC LIMIT 1",(f'position-missing:{p["id"]}',)).fetchone()
    if previous:
        label='参考仓（假定买入）' if p['mode']=='reference' else '实际成本登记仓'
        emit(db,f'position-quote-recovered:{previous[0]}',
             f'✅ {label} #{p["id"]} {p["symbol"]} 报价监测恢复\n来源 {quote.get("source","alpha_ticker")}；参考报价 {quote["price"]:.8g} USDT\n报价时间/保守下界 UTC {datetime.fromtimestamp(quote["time"]/1000,timezone.utc).isoformat()}\n未执行交易。',now)
        db.execute("UPDATE important_alerts SET state='resolved' WHERE base=? AND state IN ('open','acknowledged','expired')",(f'position-missing:{p["id"]}',))
    db.execute('INSERT OR REPLACE INTO position_quote_health VALUES(?,NULL,NULL,NULL,NULL,?,?)',
               (p['id'],quote['time'],quote.get('source','alpha_ticker')))
    price=quote['price'];peak=max(p['peak'],price);pnl=price/p['entry']-1
    label='信号参考仓（假定买入，未核实成交）' if p['mode']=='reference' else '实际成本登记仓（用户录入）'
    triggered=[]
    if price<=p['entry']*(1-p['stop_pct']):triggered.append(('stop','止损阈值触发：请核查是否减仓/退出'))
    if peak>=p['entry']*1.3 and price<=peak*(1-p['trail_pct']):triggered.append(('trail','移动止盈阈值触发：请核查是否减仓/退出'))
    for multiple in (1.5,2.,3.):
        if price>=p['entry']*multiple:triggered.append(('tp'+str(multiple),f'分批止盈观察：价格达到成本的 {multiple:g} 倍'))
    if liquidity is not None and (liquidity<100000 or (p['liquidity_base'] and liquidity<p['liquidity_base']*.5)):
        triggered.append(('liquidity','流动性风险：报告流动性低于10万美元或较登记时下降50%'))
    for kind,reason in triggered:
        cur=db.execute('INSERT OR IGNORE INTO position_alerts VALUES(?,?,?)',(p['id'],kind,now))
        if cur.rowcount:
            text=(
                 f'🔔 {label} #{p["id"]} · {p["symbol"]}\n{reason}\n'
                 f'登记成本 {p["entry"]:.8g}；报价 {price:.8g} USDT\n价格变化 {pnl:+.1%}（未扣费用/滑点）\n'
                 f'固定止损线 {p["entry"]*(1-p["stop_pct"]):.8g}；登记后已观察最高报价 {peak:.8g}\n'
                 f'地址 {p["address"]}\n报价来源 {quote.get("source","alpha_ticker")}；时间/保守下界 UTC {datetime.fromtimestamp(quote["time"]/1000,timezone.utc).isoformat()}\n'
                 '这是规则提醒，不保证能按该价格卖出；未执行交易。停止跟踪用 /close 编号。')
            if kind in ('stop','trail','liquidity'):raise_alert(db,f'position:{p["id"]}:{kind}',text,now,p['id'])
            else:emit(db,f'position:{p["id"]}:{kind}',text,now)
    recovered=[]
    if price>p['entry']*(1-p['stop_pct'])*1.02:recovered.append('stop')
    if price>peak*(1-p['trail_pct'])*1.02:recovered.append('trail')
    if liquidity is not None and liquidity>=110000 and (not p['liquidity_base'] or liquidity>p['liquidity_base']*.55):recovered.append('liquidity')
    for kind in recovered:
        resolve(db,f'position:{p["id"]}:{kind}',now)
        db.execute('DELETE FROM position_alerts WHERE position_id=? AND kind=?',(p['id'],kind))
    resolve(db,f'position-missing:{p["id"]}',now)
    db.execute('UPDATE positions SET peak=?,last_price=?,last_checked=? WHERE id=?',(peak,price,now,p['id']))

def missing_position(db,p,now,diagnostic=None):
    # One incident until recovery, including after /ack or reminder expiration.
    # A slow hourly scan must not invalidate a newer observation from the fast lane.
    from quotes import MAX_AGE_MS
    from alerts import raise_alert
    diagnostic=diagnostic or {};health=db.execute('SELECT * FROM position_quote_health WHERE position_id=?',(p['id'],)).fetchone()
    if health and health['last_quote_time'] is not None and -60000<=now*1000-health['last_quote_time']<=MAX_AGE_MS:return
    failed_since=health['failed_since'] if health and health['failed_since'] is not None else now
    reason=diagnostic.get('reason','quote_unavailable')
    db.execute('''INSERT INTO position_quote_health(position_id,failed_since,last_failure,reason) VALUES(?,?,?,?)
                  ON CONFLICT(position_id) DO UPDATE SET failed_since=excluded.failed_since,last_failure=excluded.last_failure,reason=excluded.reason''',
               (p['id'],failed_since,now,reason))
    if now-failed_since<120:return
    base=f'position-missing:{p["id"]}'
    if db.execute("SELECT 1 FROM important_alerts WHERE base=? AND state IN ('open','acknowledged','expired')",(base,)).fetchone():return
    label='参考仓（假定买入，未核实成交）' if p['mode']=='reference' else '实际成本登记仓'
    explanation={'ticker_stale_or_future':'接口返回的成交时间超出新鲜度范围',
                 'ticker_unavailable_or_invalid':'报价请求失败或返回数据无效',
                 'quote_expired_during_scan':'报价在本轮处理过程中已过期'}.get(reason,'暂无符合新鲜度要求的报价')
    stamp=diagnostic.get('last_quote_time') or (health['last_quote_time'] if health else None)
    age=f'；距最近已知成交时间/保守下界 {max(0,int(now-stamp/1000))} 秒' if stamp else ''
    aid=raise_alert(db,base,
         f'⚠️ {label} #{p["id"]} {p["symbol"]} 无法获取新鲜报价。止损/止盈监测暂时不可靠，请自行检查。\n'
         f'原因：{explanation}{age}。\n旧价格不会用于触发止盈止损；恢复后通知。\n地址 {p["address"]}',now,p['id'])
    db.execute('UPDATE position_quote_health SET alert_id=? WHERE position_id=?',(aid,p['id']))

HELP=('Alpha雷达 v3：入场参考自动建立假定买入的参考仓，不下单、不核实成交。\n'
      '/buy BSC合约地址 实际买入均价 [止损百分比 移动回撤百分比]\n'
      '例：/buy 0x… 0.012 15 20（请替换完整地址）\n'
      '默认止损15%；浮盈达到30%后启用20%移动回撤；+50%/+100%/+200%分别提示一次。阈值是可调整假设。\n'
      '/ack 提醒编号 确认已读（不会卖出）\n/pending 查看未确认重要提醒\n/positions 查看实际/参考仓\n/close 持仓编号 结束跟踪（不会卖出）\n'
      '/ranking 查看最近一期关注排序（非实时）\n/performance 前瞻效果报告\n/status 数据与模拟观察状态\n/help 查看帮助\n'
      '/trend 查看已登记实际持仓的最近走势分析（存档，非实时）\n'
      '仅支持BSC现货成本登记，不计算杠杆/强平；请勿发送 Token、私钥或助记词。')

def handle_command(db,text,data,request_key,now):
    parts=text.strip().split()
    if not parts:return HELP
    command=parts[0].split('@')[0].lower()
    if command in ('/help','/start'):return HELP
    if command in ('/trend','/analysis'):
        try:
            report=json.loads((Path(data)/'position-trends.json').read_text())
            active={p['id'] for p in open_positions(db) if p['mode']=='actual'}
            rows=[r for r in report.get('rows',[]) if r.get('position_id') in active]
            return ('最近一次实际持仓分析存档，不是即时成交报价。\n\n'+'\n\n'.join(r['text'].replace('行情有效，可供规则复核。','该结论仅对分析时有效；当前新鲜度请核对行情时间。') for r in rows)) if rows else '暂无实际持仓走势分析；参考仓不会当作真实持仓。'
        except (OSError,ValueError,KeyError):return '走势分析尚未生成或暂时不可用；不会使用旧价格判断止盈止损。'
    if command=='/ranking':
        row=db.execute("SELECT value FROM runtime WHERE key='ranking_latest'").fetchone()
        if not row:return '关注排序尚未生成。默认每6小时推送一期。'
        report=json.loads(row[0])
        if not report.get('text'):return '新版排序生成中，请稍后重试。'
        return '最近一期存档，不是实时行情；请查看生成时间。\n'+report['text']
    if command=='/ack':
        if len(parts)!=2 or not parts[1].isdigit():return '用法：/ack 重要提醒编号；只确认已读，不代表买卖。'
        cur=db.execute("UPDATE important_alerts SET state='acknowledged',acknowledged=? WHERE id=? AND state='open'",(now,int(parts[1])))
        from alerts import cancel_pending
        cancel_pending(db)
        return '已确认这条重要提醒，停止补提醒；未执行交易。' if cur.rowcount else '没有找到该编号的未确认提醒。'
    if command=='/pending':
        rows=db.execute("SELECT * FROM important_alerts WHERE state='open' ORDER BY id DESC LIMIT 30").fetchall()
        return '\n'.join(f'#{r["id"]} '+r['text'].split('\n')[0]+f'；/ack {r["id"]}' for r in rows) if rows else '没有未确认的重要提醒。'
    if command=='/positions':
        rows=open_positions(db)
        if not rows:return '目前没有实际登记仓或信号参考仓。新的突破/回踩参考会自动建立假定买入仓；实际成交后用 /buy 地址 均价 校正。'
        return '\n'.join(f'#{p["id"]} {p["symbol"]} [{"假定买入/非成交" if p["mode"]=="reference" else "实际成本登记"}] 成本 {p["entry"]:.8g} 止损 {p["stop_pct"]:.0%} 移动回撤 {p["trail_pct"]:.0%}\n地址 {p["address"]}\n最新监测：'+(f'{p["last_price"]:.8g} USDT，UTC {datetime.fromtimestamp(p["last_checked"],timezone.utc).isoformat()}' if p['last_checked'] else '等待报价') for p in rows)
    if command=='/close':
        if len(parts)!=2 or not parts[1].isdigit():return '用法：/close 持仓编号；只结束提醒，不执行卖出。'
        cur=db.execute('UPDATE positions SET closed=? WHERE id=? AND closed IS NULL',(now,int(parts[1])))
        changed=cur.rowcount
        db.execute("UPDATE important_alerts SET state='resolved' WHERE position_id=? AND state='open'",(int(parts[1]),))
        from alerts import cancel_pending
        cancel_pending(db)
        return '已结束该持仓的跟踪。未执行任何交易。' if changed else '没有找到该编号的未关闭持仓。'
    if command=='/performance':
        try:r=json.loads((Path(data)/'evaluation-report.json').read_text())
        except (OSError,ValueError):return '前瞻效果报告尚未生成。'
        return (f'效果复盘 UTC {r.get("as_of")}\n路径记录 {r.get("tracks")} 条；版本 {r.get("rule_version")}\n'+
                '\n'.join(f'{g["role"]}/{g["stage"]}：{g["tracks"]}条，满90天{g["mature_90d"]}条，观察触及100倍{g["observed_100x"]}条' for g in r.get('groups',[]))+
                '\n采样高点不是实际收益，同币多信号不是独立样本；尚无百倍预测胜率。')
    if command=='/status':
        try:r=json.loads((Path(data)/'strategy-latest.json').read_text())
        except (OSError,ValueError):return '小时策略尚未完成首次扫描。'
        return (f'小时策略 {r.get("version")}\n扫描 UTC {r.get("as_of")}\n状态 {r.get("status")}；错误 {len(r.get("errors",[]))}\n'
                f'BSC Alpha名录 {r.get("universe_count")}；本轮小时检查 {r.get("checked")}；缺新鲜报价跳过 {len(r.get("skipped",[]))}\n'
                f'纸面跟踪 {db.execute("SELECT count(*) FROM paper_tracks").fetchone()[0]} 条；实际/参考仓共 {len(open_positions(db))} 条（/positions 区分）\n'
                '纸面观察是从实际触发时开始积累，尚无预测百倍收益的验证结论。')
    if command!='/buy':return '未知命令。发送 /help 查看用法。'
    if len(parts) not in (3,5):return '用法：/buy BSC合约地址 实际买入均价 [止损百分比 移动回撤百分比]'
    address=parts[1].lower();price=num(parts[2]);stop=num(parts[3]) if len(parts)==5 else 15.;trail=num(parts[4]) if len(parts)==5 else 20.
    if not re.fullmatch(r'0x[0-9a-f]{40}',address) or price is None or not 0<price<1e15:return '地址或价格无效。需要完整BSC地址和大于0的有限现货均价。'
    if stop is None or trail is None or not 1<=stop<=80 or not 1<=trail<=80:return '止损和移动回撤需在1–80之间，例如15 20。'
    existing=db.execute('SELECT id,mode FROM positions WHERE address=? AND closed IS NULL',(address,)).fetchone()
    if existing and existing['mode']=='actual':return f'该地址已登记为 #{existing[0]}。若成本发生变化，请先 /close {existing[0]} 再重新登记；峰值将从新登记时开始。'
    if db.execute("SELECT count(*) FROM positions WHERE closed IS NULL AND mode='actual'").fetchone()[0]>=20:return '最多同时跟踪20个实际持仓。请先关闭不再持有的记录。'
    try:directory=json.loads((Path(data)/'strategy-directory.json').read_text())
    except (OSError,ValueError):return '名录暂不可用，请稍后重试。'
    if now-directory.get('observed',0)>1800:return '名录已超过30分钟未更新，暂不接受新持仓；现有持仓继续尝试监测。'
    token=directory.get('tokens',{}).get(address)
    if not token:return '当前有效BSC Alpha名录找不到该地址。请核对链与合约地址。'
    if existing:
        db.execute('UPDATE positions SET closed=? WHERE id=?',(now,existing['id']))
        db.execute("UPDATE important_alerts SET state='resolved' WHERE position_id=? AND state='open'",(existing['id'],))
        from alerts import cancel_pending
        cancel_pending(db)
    cur=db.execute('INSERT INTO positions(request_key,address,symbol,alpha_id,entry,created,stop_pct,trail_pct,peak,liquidity_base) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (request_key,address,token['symbol'],token['alphaId'],price,now,stop/100,trail/100,price,num(token.get('liquidity'))))
    return (f'已登记现货持仓 #{cur.lastrowid} {token["symbol"]}，成本 {price:.8g} USDT。\n固定止损 {price*(1-stop/100):.8g}（-{stop:g}%）；浮盈30%后启用{trail:g}%移动回撤。\n'
            '这只是提醒配置，不是成交记录核验或自动卖出。峰值从登记后采样，不追溯登记前行情。')


def reference_position(db,token,quote,key,now):
    """Reference only; never overwrite an actual cost or compound repeated signal entries."""
    address=token['contractAddress'].lower()
    existing=db.execute('SELECT * FROM positions WHERE address=? AND closed IS NULL',(address,)).fetchone()
    if existing:
        label='实际登记仓' if existing['mode']=='actual' else '信号参考仓'
        return f'继续跟踪{label} #{existing["id"]}，原成本 {existing["entry"]:.8g} USDT；本信号不重复建仓/加仓。'
    if db.execute("SELECT count(*) FROM positions WHERE closed IS NULL AND mode='reference'").fetchone()[0]>=20:
        return '⚠️ 已达20个信号参考仓上限，本次没有新增止盈止损跟踪；请 /positions 检查并关闭不需要的记录。'
    price=quote['price']
    cur=db.execute("INSERT INTO positions(request_key,address,symbol,alpha_id,entry,created,stop_pct,trail_pct,peak,liquidity_base,mode) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ('reference:'+key,address,token['symbol'],token['alphaId'],price,now,.15,.2,price,num(token.get('liquidity')),'reference'))
    return (f'信号参考仓 #{cur.lastrowid}：假定买入价 {price:.8g} USDT（未核实成交）。\n'
            f'固定止损参考 {price*.85:.8g}；分批止盈参考 {price*1.5:.8g} / {price*2:.8g} / {price*3:.8g}。\n'
            '浮盈30%后启用20%移动回撤；按采样报价监测，不保证成交。实际成交价不同可 /buy 地址 均价 校正。')
