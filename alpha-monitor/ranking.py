"""Versioned attention ranking from fresh observations, with durable periodic delivery."""
import json, os, time
from datetime import datetime, timezone, timedelta
from radar import num, utc

VERSION='focus-v1.1'
DEFAULT_INTERVAL=21600

def interval_seconds(env=None):
    env=os.environ if env is None else env
    try:value=int(env.get('RANKING_INTERVAL_SECONDS',str(DEFAULT_INTERVAL)))
    except ValueError:raise ValueError('RANKING_INTERVAL_SECONDS must be an integer') from None
    if not 3600<=value<=86400:raise ValueError('RANKING_INTERVAL_SECONDS must be 3600..86400')
    return value

def score(o,now):
    f=o.get('features') or {}; t=o.get('token') or {}; q=o.get('quote') or {}; risk=o.get('safety') or {}
    if o.get('blocks') or risk.get('flags') or risk.get('unlock',{}).get('large_unlock_soon'):return None
    price=num(q.get('price'));ts=num(q.get('time'))
    if not price or price<=0 or ts is None or not 0<=now*1000-ts<=180000:return None
    if f.get('end')!=int(now*1000)//3600000*3600000-1:return None
    values={k:num(f.get(k)) for k in ('volume_ratio','level','close','return_1h','return_24h','quote_volume')}
    if any(v is None for v in values.values()) or values['level']<=0:return None
    cap=num(t.get('marketCap'));fdv=num(t.get('fdv'));liq=num(t.get('liquidity'))
    if cap is None or not 0<cap<=1e8 or not fdv or fdv<=0 or not .1<=cap/fdv<=1.05 or liq is None or liq<1e5:return None
    # Attention list does not promote falling or heavily extended prices.
    distance=price/values['level']
    if not .9<=distance<=1.08 or not 0<=values['return_1h']<=.2 or not 0<=values['return_24h']<=.6 or values['quote_volume']<10000:return None
    dual=bool(o.get('dual_matched'))
    setup_seen=num(o.get('setup_seen'))
    active=bool(o.get('setup') and setup_seen is not None and 0<=now-setup_seen<86400 and not o.get('invalid'))
    components={
        'volume':25*min(max(values['volume_ratio']-1,0)/3,1),
        'near_level':20*max(0,1-abs(distance-1)/.10),
        'momentum':10*min(values['return_1h']/.05,1)+5*min(values['return_24h']/.2,1),
        'liquidity':15*min(liq/1e6,1),
        'size':10*(1-cap/1e8),
        'supply':5*min(cap/fdv/.5,1),
        'setup':10 if active else 0,
    }
    points=round(sum(components.values()),1)
    reason=[f'小时量比{values["volume_ratio"]:.2f}',f'距前24小时高点{distance-1:+.1%}']
    stage='已有小时入场参考' if active else '仅关注，尚无有效入场参考'
    if active and o.get('retested'):stage='已有回踩确认参考'
    return {'symbol':str(o.get('symbol','?'))[:24],'address':o['address'],'score':points,'components':components,
            'price':price,'quote_time':ts,'stage':stage,'reason':'；'.join(reason),'dual_matched':dual,
            'market':'Alpha＋USDT永续' if dual else '仅Alpha观察池',
            'market_cap':cap,'liquidity':liq,'safety_status':risk.get('status','not_checked'),
            'unlock_status':risk.get('unlock',{}).get('status','unknown'),
            # This has no score impact. It remains provenance for the values
            # displayed in the focus list, rather than an unvalidated signal.
            'crypto_market_rank':o.get('crypto_market_rank')}

def rank(observations,now=None,limit=5):
    now=time.time() if now is None else now;unique={}
    for o in observations:
        row=score(o,now)
        if row:unique[row['address']]=row
    # User's Alpha + futures intersection is primary; Alpha-only is explicitly secondary.
    return sorted(unique.values(),key=lambda r:(not r['dual_matched'],-r['score'],r['address']))[:limit]

def format_message(rows,now=None,coverage=None,previous=None):
    now=time.time() if now is None else now;previous=previous or {};coverage=coverage or {}
    local=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')
    lines=[f'📊 候选关注排序 · 北京时间{local} · {VERSION}',
           'Alpha＋合约优先，最多5名；关注分不是百倍概率，不自动建仓。',
           f'本轮尝试{coverage.get("checked",0)}个，缺新鲜报价跳过{coverage.get("skipped",0)}个；数据异常{coverage.get("errors",0)}项。']
    detail=coverage.get('error_breakdown') or {}
    if detail:
        lines.append('数据缺失分类：'+'；'.join(f'{k}×{v}' for k,v in sorted(detail.items())))
    if not rows:lines.append('本轮无合格候选，不凑数；缺失数据不代表没有机会。')
    for i,r in enumerate(rows,1):
        old=previous.get(r['address']);change='新入榜' if old is None else ('持平' if old==i else f'{"上升" if old>i else "下降"}{abs(old-i)}位')
        security='第三方未检出所列风险，仍非安全保证' if r['safety_status']=='no_listed_risk_detected' else '安全数据未知/未检查'
        lines += [f'{i}. {r["symbol"]} {r["score"]}/100 · {change} · {r["market"]}',
                  f'{r["stage"]}；{r["reason"]}',
                  f'报价{r["price"]:.8g} USDT；市值{r["market_cap"]/1e6:.2f}百万；流动性{r["liquidity"]/1e3:.0f}千美元',
                  security+'；解锁'+('已有披露待复核' if r['unlock_status']!='unknown' else '未知'),
                  '地址 '+r['address']]
        evidence=r.get('crypto_market_rank') or {}
        if evidence.get('status')=='ok':
            pieces=[f'Binance Alpha榜 #{evidence.get("rank")}']
            if evidence.get('price') is not None: pieces.append(f'榜单价 {evidence["price"]:.8g}')
            if evidence.get('liquidity') is not None: pieces.append(f'榜单流动性 {evidence["liquidity"]/1e3:.0f}千')
            if evidence.get('holders_top10_percent') is not None: pieces.append(f'原始Top10 {evidence["holders_top10_percent"]:.1f}%')
            lines.append('；'.join(pieces)+'；仅交叉数据，不加分且未清洗池/交易所地址。')
        elif evidence.get('status')=='stale':
            lines.append('Binance Alpha榜数据已过期，本次不采用。')
    if previous:
        left=[a for a in previous if a not in {r['address'] for r in rows}]
        if left:lines.append(f'上期有{len(left)}个退出榜单，可能因排名或数据变化；退出不等于卖出。')
    lines.append('关联钱包与清洗后集中度仍未核实；价格为生成时快照。规则未经样本外校准，榜单不替代独立止盈止损提醒。')
    return '\n'.join(lines)

def maybe_emit(store,report,now=None,interval=None):
    now=time.time() if now is None else now;interval=interval_seconds() if interval is None else interval
    bucket=int(now//interval);key=f'ranking:{VERSION}:{bucket}'
    rows=rank((report or {}).get('observations',[]),now)
    skipped=(report or {}).get('skipped',[])
    errors=(report or {}).get('errors',[])
    breakdown={}
    for item in list(skipped)+list(errors):
        reason=item.get('error','unknown') if isinstance(item,dict) else str(item)
        breakdown[reason]=breakdown.get(reason,0)+1
    coverage={'checked':(report or {}).get('checked',0),'skipped':len(skipped),
              'errors':len(errors) if report else 1,'error_breakdown':breakdown}
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        r=db.execute("SELECT value FROM runtime WHERE key='ranking_last_bucket'").fetchone()
        if r and json.loads(r[0])=={'version':VERSION,'bucket':bucket}:return False
        r=db.execute("SELECT value FROM runtime WHERE key='ranking_latest'").fetchone()
        old=json.loads(r[0]).get('rows',[]) if r else []
        previous={r['address']:i for i,r in enumerate(old,1)}
        text=format_message(rows,now,coverage,previous)
        snapshot={'as_of':utc(now*1000),'version':VERSION,'rows':rows,'coverage':coverage,'interval_seconds':interval,'text':text}
        db.execute('INSERT OR IGNORE INTO outbox(key,created,payload) VALUES(?,?,?)',(key,now,json.dumps({'kind':'ranking','text':text},ensure_ascii=False)))
        for k,v in (('ranking_last_bucket',{'version':VERSION,'bucket':bucket}),('ranking_latest',snapshot)):
            db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',(k,json.dumps(v,ensure_ascii=False)))
    return True
