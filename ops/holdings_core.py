"""Actual-cost journals and deterministic trend references. No execution or model calls."""
import json,math,re,sqlite3,time
from datetime import datetime,timezone

VERSION='holdings-v1'
CHAINS=('bsc','solana','robinhood','arc','stable','xlayer')

def number(value):
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError):return None

def initialize(db):
    db.row_factory=sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY,domain TEXT,market TEXT,instrument TEXT,
      entry REAL,quantity REAL,created REAL,closed REAL,peak REAL,stop_pct REAL,trail_pct REAL);
    CREATE UNIQUE INDEX IF NOT EXISTS active_position ON positions(domain,market,instrument) WHERE closed IS NULL;
    CREATE TABLE IF NOT EXISTS commands(key TEXT PRIMARY KEY,reply TEXT);
    CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS reviews(key TEXT PRIMARY KEY,checked REAL,quote_time REAL,payload TEXT);
    CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY,domain TEXT,channel TEXT,created REAL,expires REAL,
      text TEXT,state TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,next_try REAL DEFAULT 0,sent REAL,error TEXT);
    ''')

def help_text(domain):
    buy=('/buy bsc 0x完整合约地址 实际均价 [数量]\n链支持 bsc/solana/robinhood/arc/stable/xlayer；Meme成本单位 USD。' if domain=='meme' else
         '/buy a 600519.SS 实际均价 [股数]\n/buy hk 0700.HK 实际均价 [股数]\n/buy us NVDA 实际均价 [股数]\nA股成本CNY、港股HKD、美股USD；数量按股，不是手。')
    return ('实际持仓登记（不下单）：\n'+buy+'\n/buy 重复登记同一标的会更新整仓均价/数量，并重置观察起点；不是追加一笔成交。\n'
            '/positions 查看实际持仓\n/trend [编号] 查看最近一次走势分析\n/close 编号 结束跟踪，不代表卖出\n'
            '/risk 编号 止损百分比 移动回撤百分比（如 /risk 1 10 15）\n'
            '均价填真实成交后的持仓成本，不填提醒价；数量可省略，不影响百分比分析。仅接受机器人已配置接收人的私聊指令。')

def identity(domain,market,instrument):
    market=market.lower()
    if domain=='meme':
        if market not in CHAINS:raise ValueError('链名无效')
        pattern=r'[1-9A-HJ-NP-Za-km-z]{32,44}' if market=='solana' else r'0x[0-9a-fA-F]{40}'
        if not re.fullmatch(pattern,instrument):raise ValueError('需要该链完整合约地址')
        return market,instrument if market=='solana' else instrument.lower()
    if domain!='stock':raise ValueError('不支持的登记入口')
    instrument=instrument.upper()
    if market=='a':
        if not re.fullmatch(r'\d{6}\.(SS|SZ|BJ)',instrument):raise ValueError('A股请填写带交易所后缀的代码，如600519.SS、000001.SZ')
    elif market=='hk':
        if not re.fullmatch(r'\d{4,5}\.HK',instrument):raise ValueError('港股请填写如0700.HK')
        instrument=instrument.split('.')[0].zfill(4)+'.HK'
    elif market=='us':
        if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}',instrument) or instrument.endswith(('.HK','.SS','.SZ','.BJ')):raise ValueError('美股代码无效')
    else:raise ValueError('股票市场填a、hk或us')
    return market,instrument

def command(db,domain,text,request_key,now):
    cached=db.execute('SELECT reply FROM commands WHERE key=?',(request_key,)).fetchone()
    if cached:return cached[0]
    parts=text.strip().split();verb=parts[0].split('@')[0].lower() if parts else '/help'
    try:
        if verb=='/buy':
            if len(parts) not in (4,5):raise ValueError('格式：/buy 市场或链 完整代码或地址 实际均价 [数量]')
            market,instrument=identity(domain,parts[1],parts[2]);entry=number(parts[3]);qty=number(parts[4]) if len(parts)==5 else None
            if entry is None or not 0<entry<1e15 or (len(parts)==5 and (qty is None or not 0<qty<1e20)):raise ValueError('均价和数量必须是大于0的有限数值')
            old=db.execute('SELECT id FROM positions WHERE domain=? AND market=? AND instrument=? AND closed IS NULL',(domain,market,instrument)).fetchone()
            if old:
                pid=old[0];db.execute('UPDATE positions SET entry=?,quantity=?,created=?,peak=? WHERE id=?',(entry,qty,now,entry,pid))
                db.execute('DELETE FROM reviews WHERE key=?',(domain+':'+str(pid),))
            else:
                if db.execute('SELECT count(*) FROM positions WHERE domain=? AND closed IS NULL',(domain,)).fetchone()[0]>=30:raise ValueError('每套雷达最多登记30个实际持仓，请先结束不用的跟踪')
                cur=db.execute('INSERT INTO positions(domain,market,instrument,entry,quantity,created,peak,stop_pct,trail_pct) VALUES(?,?,?,?,?,?,?,?,?)',
                               (domain,market,instrument,entry,qty,now,entry,.08 if domain=='stock' else .15,.12 if domain=='stock' else .20));pid=cur.lastrowid
            currency={'a':'CNY','hk':'HKD','us':'USD'}.get(market,'USD')
            db.execute("UPDATE outbox SET state='cancelled' WHERE domain=? AND state='pending' AND (key LIKE ? OR key LIKE ?)",(domain,f'risk:{domain}:{pid}:%',f'summary:{domain}:%'))
            db.execute('DELETE FROM runtime WHERE key=?',('risk:'+domain+':'+str(pid),))
            reply=f'已登记实际持仓 #{pid} · {instrument}\n整仓成本 {entry:g} {currency}；数量 {qty if qty is not None else "未填"}。\n身份与行情尚待数据源核验，约5分钟内尝试生成走势分析；无新鲜行情时不触发止盈止损结论。\n/trend {pid} 查看；/close {pid} 结束跟踪。未执行交易。'
        elif verb=='/positions':
            rows=db.execute('SELECT * FROM positions WHERE domain=? AND closed IS NULL ORDER BY id',(domain,)).fetchall()
            reply='\n'.join(f'#{p["id"]} {p["market"]} {p["instrument"]}；实际均价 {p["entry"]:g}；数量 {p["quantity"] if p["quantity"] is not None else "未填"}' for p in rows) or '尚未登记实际持仓。\n'+help_text(domain)
        elif verb=='/close':
            if len(parts)!=2 or not parts[1].isdigit():raise ValueError('用法：/close 持仓编号')
            changed=db.execute('UPDATE positions SET closed=? WHERE id=? AND domain=? AND closed IS NULL',(now,int(parts[1]),domain)).rowcount
            if changed:db.execute("UPDATE outbox SET state='cancelled' WHERE domain=? AND state='pending' AND (key LIKE ? OR key LIKE ?)",(domain,f'risk:{domain}:{int(parts[1])}:%',f'summary:{domain}:%'))
            reply='已结束跟踪；未执行卖出。' if changed else '没有找到该机器人下的活动持仓。'
        elif verb=='/risk':
            if len(parts)!=4 or not parts[1].isdigit():raise ValueError('用法：/risk 编号 止损百分比 移动回撤百分比')
            stop,trail=number(parts[2]),number(parts[3])
            if stop is None or trail is None or not 1<=stop<=90 or not 1<=trail<=90:raise ValueError('百分比填写1到90之间的数字，不加百分号')
            pid=int(parts[1]);changed=db.execute('UPDATE positions SET stop_pct=?,trail_pct=? WHERE id=? AND domain=? AND closed IS NULL',(stop/100,trail/100,pid,domain)).rowcount
            if changed:
                db.execute('DELETE FROM reviews WHERE key=?',(domain+':'+str(pid),))
                db.execute("UPDATE outbox SET state='cancelled' WHERE domain=? AND state='pending' AND (key LIKE ? OR key LIKE ?)",(domain,f'risk:{domain}:{pid}:%',f'summary:{domain}:%'))
            reply=f'已更新 #{pid}：成本止损参考 {stop:g}%，浮盈曾达30%后移动回撤 {trail:g}%。未执行交易。' if changed else '没有找到该机器人下的活动持仓。'
        elif verb in ('/trend','/analysis'):
            if len(parts)>2 or (len(parts)==2 and not parts[1].isdigit()):raise ValueError('用法：/trend [持仓编号]')
            rows=db.execute('SELECT p.id,r.payload FROM positions p LEFT JOIN reviews r ON r.key=p.domain||\':\'||p.id WHERE p.domain=? AND p.closed IS NULL ORDER BY p.id',(domain,)).fetchall()
            selected=[r for r in rows if len(parts)==1 or r['id']==int(parts[1])]
            reply='以下是最近一次分析存档，不是即时成交报价。\n\n'+'\n\n'.join(json.loads(r['payload'])['text'].replace('行情有效，可供规则复核。','该结论仅对分析时有效；当前新鲜度请核对行情时间。') if r['payload'] else f'#{r["id"]} 等待行情核验与走势分析' for r in selected) if selected else '没有可查看的实际持仓。先用 /buy 登记。'
        else:reply=help_text(domain)
    except ValueError as exc:reply=str(exc)+'\n/help 查看格式；未执行交易。'
    db.execute('INSERT INTO commands VALUES(?,?)',(request_key,reply))
    return reply

def authorized_update(update,chat,now):
    msg=update.get('message') or {}
    return (isinstance(update.get('update_id'),int) and str((msg.get('chat') or {}).get('id'))==str(chat)
            and (msg.get('chat') or {}).get('type')=='private' and str((msg.get('from') or {}).get('id'))==str(chat)
            and isinstance(msg.get('date'),(int,float)) and -30<=now-msg['date']<=600
            and not any(k.startswith('forward_') for k in msg) and isinstance(msg.get('text'),str))

def analyze(position,quote,bars,now,frame,safety=None):
    """bars: sorted closed OHLC records in seconds. Source adapters validate identity and time."""
    p=position;entry=p['entry'];price=number((quote or {}).get('price'));stamp=number((quote or {}).get('time'))
    max_age=900 if p['domain']=='stock' else 600 if p['domain']=='meme' else 180
    fresh=price is not None and price>0 and stamp is not None and 0<=now-stamp<=max_age
    valid=[]
    for b in bars:
        vals=[number(b.get(k)) for k in ('time','end','open','high','low','close')]
        if any(v is None for v in vals):continue
        t,end,o,h,l,c=vals
        if end>now or end<=t or min(o,h,l,c)<=0 or not l<=min(o,c)<=max(o,c)<=h:continue
        valid.append(b)
    valid=sorted({b['time']:b for b in valid}.values(),key=lambda b:b['time'])
    # Intraday gaps invalidate a continuous moving-average/ATR trend calculation.
    steps={'1h':3600,'5m':300}
    window=valid[-51:]
    contiguous=frame not in steps or (valid and all(y['time']-x['time']==steps[frame] for x,y in zip(window,window[1:])))
    recent=bool(valid) and now-valid[-1]['end']<=(3*86400 if frame=='1d' else steps.get(frame,3600)*2)
    enough=len(valid)>=21 and contiguous and recent
    closes=[b['close'] for b in valid]
    ma20=sum(closes[-20:])/20 if enough else None
    ma50=sum(closes[-50:])/50 if enough and len(valid)>=50 else None
    atr=sum(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(valid[-15:-1],valid[-14:]))/14 if enough else None
    support=min(b['low'] for b in valid[-21:-1]) if enough else None
    resistance=max(b['high'] for b in valid[-21:-1]) if enough else None
    trend='历史K线不足、存在缺口或已过期'
    if ma20 is not None:
        basis=price if fresh else closes[-1]
        trend='偏强' if ma50 is not None and basis>ma20>ma50 else '偏弱' if ma50 is not None and basis<ma20<ma50 else '震荡/方向未确认'
    peak=max(p.get('peak') or entry,price) if fresh else p.get('peak') or entry
    fixed=entry*(1-p['stop_pct']);trail=peak*(1-p['trail_pct']) if peak>=entry*1.3 else None
    flags=[]
    if fresh:
        if price<=fixed:flags.append('触及登记止损参考线')
        if trail is not None and price<=trail:flags.append('触及盈利后的移动回撤参考线')
        if support is not None and price<support:flags.append('跌破此前20根K线低点参考')
        for multiple in (1.5,2.,3.):
            if price>=entry*multiple:flags.append(f'达到成本{multiple:g}倍，可复核分批止盈计划')
    market_label={'alpha':'币安Alpha','meme':'Meme','stock':{'a':'A股','hk':'港股','us':'美股'}.get(p['market'],'股票')}[p['domain']]
    title=f'持仓走势参考 · {market_label} #{p["id"]} · {p.get("symbol") or p["instrument"]}'
    status=f'参考价 {price:.8g} {quote.get("currency","USD")}；成本 {entry:.8g}；价格变化 {price/entry-1:+.1%}（未扣费用/滑点）' if price is not None and price>0 else '暂无可用报价'
    stamp_text=datetime.fromtimestamp(stamp,timezone.utc).strftime('%m-%d %H:%M:%S UTC') if stamp is not None else '未知'
    lines=[title,'实际成本登记仓；不代表已核对交易所成交。',
           '分析生成 '+datetime.fromtimestamp(now,timezone.utc).strftime('%m-%d %H:%M:%S UTC'),status,
           f'行情时间/保守下界 {stamp_text}；来源 {(quote or {}).get("source","未知")}',
           ('行情有效，可供规则复核。' if fresh else '休市、旧报价或数据缺失：暂停当前止盈止损判断。'),
           f'{frame}走势：{trend}；有效K线 {len(valid)} 根。']
    if enough:
        lines.append(f'MA20 {ma20:.8g}；'+(f'MA50 {ma50:.8g}；' if ma50 else '')+f'ATR14 {atr:.8g}')
        lines.append(f'前20根支撑/压力参考 {support:.8g} / {resistance:.8g}；不是必然反转点。')
    lines.append(f'固定止损参考 {fixed:.8g}（成本下方{p["stop_pct"]:.0%}）；登记后观察高点 {peak:.8g}。')
    if trail is not None:lines.append(f'浮盈曾达30%后的移动回撤参考 {trail:.8g}（{p["trail_pct"]:.0%}）。')
    if atr is not None and peak-2*atr>0:lines.append(f'波动参考线（观察高点−2×ATR）{peak-2*atr:.8g}，仅分析，不替代登记阈值。')
    lines.append('分批止盈观察：成本的1.5/2/3倍；阈值未经本账户收益验证。')
    if flags:lines.append('🔔 '+'；'.join(flags)+'。请结合可成交价格复核，不自动卖出。')
    if safety:lines.append('卖出/流动性安全：'+safety+'；报价不证明可以成交。')
    lines.append('不预测百倍收益，不执行交易。')
    return {'version':VERSION,'position_id':p['id'],'analyzed_at':now,'fresh':fresh,'flags':flags,'peak':peak,'quote_time':stamp,'frame':frame,'trend':trend,
            'ma20':ma20,'ma50':ma50,'atr14':atr,'support':support,'resistance':resistance,'text':'\n'.join(lines)}
