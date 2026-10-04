"""Exploratory hourly setups, prospective paper observations and manual-position exits."""
from __future__ import annotations
import concurrent.futures,json,statistics,time
from pathlib import Path
from radar import ALPHA,TOKENS,FUTURES,PublicAPI,candles,num,ratio,utc,dump,universe
from safety import inspect,unlock_override,entity_evidence
from positions import VERSION,initialize,emit,open_positions,position_tick,missing_position,reference_position
from quotes import get_quote,valid
from binance_rank import fetch as fetch_market_rank, attach as attach_market_rank

HOUR=3600000

def fresh_quote(raw,now):
    if not isinstance(raw,dict):raise ValueError('ticker_schema')
    p=num(raw.get('lastPrice'));ts=num(raw.get('closeTime'))
    if p is None or p<=0 or ts is None or not -60000<=now*1000-ts<=180000:raise ValueError('ticker_missing_or_stale')
    return {'price':p,'time':ts}

def hourly_features(bars,now):
    end=int(now*1000)//HOUR*HOUR-1
    if len(bars)<25:return None
    b=bars[-25:];last=b[-1]
    if last['end']!=end or any(y['t']-x['t']!=HOUR for x,y in zip(b,b[1:])):return None
    med=statistics.median(x['q'] for x in b[:-1]);level=max(x['h'] for x in b[:-1])
    return {'end':last['end'],'open':last['o'],'close':last['c'],'low':last['l'],'high':last['h'],
            'volume_ratio':last['q']/med,'quote_volume':last['q'],'level':level,
            'return_1h':last['c']/last['o']-1,'return_24h':last['c']/b[0]['c']-1}

def gates(token,dual=None):
    out=[];cap=num(token.get('marketCap'));fdv=num(token.get('fdv'));liq=num(token.get('liquidity'));vol=num(token.get('volume24h'))
    if cap is None or not 0<cap<=100e6:out.append('流通市值缺失/超过1亿美元')
    if not fdv or not cap or cap>fdv*1.05 or cap/fdv<.1:out.append('供应比例缺失/矛盾/低于10%')
    if liq is None or liq<100000:out.append('流动性缺失/低于10万美元')
    if vol is None or vol<100000:out.append('24h成交额缺失/低于10万USDT')
    change=num(token.get('percentChange24h'))
    if change is None or change>60:out.append('24h涨幅缺失/超过60%，避免追高')
    if dual is not None:out.extend(dual.get('blocks',[]))
    return list(dict.fromkeys(out))

def transition(state,fx,quote,blocked,now):
    """Only the latest fully closed hour can trigger. Retest requires an earlier observed breakout."""
    s=dict(state);events=[];price=quote['price']
    active=s.get('setup_end') and now-s.get('setup_seen',0)<86400 and not s.get('invalid')
    if active:
        invalid=blocked or (fx and fx['end']>s['setup_end'] and fx['close']<s['level']*.97) or price<s['level']*.95
        if invalid:
            s['invalid']=True;events.append(('invalidated',s['setup_end'],s['level'],'触发过滤门槛或跌破突破支撑，原入场观察失效'))
        elif fx and fx['end']>s['setup_end'] and not s.get('retested') and fx['end']-s['setup_end']<=12*HOUR:
            if s['level']*.95<=fx['low']<=s['level']*1.02 and fx['close']>=max(s['level'],fx['open']) and s['level']*.98<=price<=s['level']*1.05:
                s['retested']=True;events.append(('retest',fx['end'],s['level'],'此前已观察突破，随后12小时内回踩并收回支撑'))
    if fx is None or blocked or fx['end']==s.get('processed_end'):return s,events
    s['processed_end']=fx['end']
    active=s.get('setup_end') and now-s.get('setup_seen',0)<86400 and not s.get('invalid')
    if fx['quote_volume']<10000 or not 0<=fx['return_24h']<=.6 or not 0<fx['return_1h']<=.2:return s,events
    if not active and fx['volume_ratio']>=2.5 and fx['level']<fx['close']<=fx['level']*1.08 and fx['level']<=price<=fx['close']*1.03:
        s.update(setup_end=fx['end'],setup_seen=now,level=fx['level'],invalid=False,retested=False)
        events.append(('breakout',fx['end'],fx['level'],'小时收盘放量突破前24小时高点，当前报价未明显追高'))
    elif not active and fx['volume_ratio']>=2 and fx['level']*.95<=fx['close']<=fx['level'] and price<=fx['level']*1.03 and now-s.get('watch_seen',0)>=21600:
        s['watch_seen']=now;events.append(('watch',fx['end'],fx['level'],'放量接近前24小时高点，尚未确认突破'))
    return s,events

def render(kind,token,market,fx,quote,level,reason,blocks,dual,now):
    labels={'watch':'提前观察（未确认突破）','breakout':'入场参考：小时突破','retest':'入场参考：回踩确认','invalidated':'原信号失效 / 风险提醒'}
    cap=num(token.get('marketCap'));liq=num(token.get('liquidity'))
    return (f'🔎 {labels[kind]} · {token["symbol"]}\n{market}\n{reason}\n'
            f'参考报价 {quote["price"]:.8g} USDT；支撑参考 {level:.8g}\n'
            f'失效参考：小时收盘低于 {level*.97:.8g}，或新鲜报价低于 {level*.95:.8g}\n'
            f'小时量比 {fx["volume_ratio"]:.2f}；小时涨幅 {fx["return_1h"]:+.1%}\n' if fx else
            f'🔎 {labels[kind]} · {token["symbol"]}\n{market}\n{reason}\n参考报价 {quote["price"]:.8g} USDT\n') + (
            f'流通市值 {cap or 0:,.0f} USD；报告流动性 {liq or 0:,.0f} USD\n'
            f'100倍静态流通市值 {(cap or 0)*100:,.0f} USD（供应不变假设）\n'
            f'地址 {token["contractAddress"]}\n报价 UTC {utc(quote["time"])}\n'
            f'规则 {VERSION}：小时阈值为新增待验证假设，不是历史百倍规律的证明。\n'
            +('风险：'+'；'.join(blocks+(dual or {}).get('risk_notes',[]))+'\n' if blocks or (dual or {}).get('risk_notes') else '')+
            '持仓集中/关联钱包/解锁/卖出安全未知；仅研究参考，不保证100倍、不执行买卖。')

def paper_tick(db,address,quote,now):
    for p in db.execute('SELECT * FROM paper_tracks WHERE address=? AND created>?',(address,now-8*86400)).fetchall():
        age=now-p['created'];marks=json.loads(p['marks']);price=quote['price']
        # An observation within 30min after each horizon, otherwise explicitly missing; no fabricated interpolation.
        for hours in (24,72,168):
            if age>=hours*3600 and str(hours) not in marks:
                marks[str(hours)]={'return':price/p['entry']-1,'observed_at':now} if age-hours*3600<=1800 else {'missing':True}
        db.execute('UPDATE paper_tracks SET last=?,peak=?,trough=?,checked=?,samples=samples+1,marks=? WHERE key=?',
                   (price,max(price,p['peak']),min(price,p['trough']),now,json.dumps(marks),p['key']))

def scan(args,store,daily=None):
    now=time.time();base=Path(args.data);api=PublicAPI(base/'raw',ttl=20)
    errors=[];skipped=[]
    with store.db() as db:
        initialize(db)
        import evaluation
        evaluation.initialize(db)
    try:
        tokens=api.data(TOKENS)
        if not isinstance(tokens,list) or not tokens:raise ValueError('empty_alpha_directory')
        active=[t for t in tokens if str(t.get('chainId'))=='56' and not any(t.get(k) for k in ('offline','offsell','fullyDelisted')) and t.get('alphaId') and t.get('contractAddress')]
        if not active:raise ValueError('empty_bsc_directory')
        byaddr={t['contractAddress'].lower():t for t in active}
        dump(base/'strategy-directory.json',{'observed':now,'tokens':byaddr})
    except Exception:
        byaddr={};errors.append({'stage':'directory','error':'unavailable'})
    # Binance Web3's Alpha board is useful cross-market evidence only when its
    # chain/address identity is exact. It never supplies a position quote.
    rank_snapshot,rank_health=fetch_market_rank(api, '56')
    rank_health['matched']=attach_market_rank(list(byaddr.values()),rank_snapshot,now)
    duals={v['address'].lower():v for v in (daily or {}).get('candidates',[]) if v.get('chain_id')=='56'}
    # Official futures directory is required before labelling an asset Alpha-only.
    try:
        exchange=api.data(FUTURES+'/fapi/v1/exchangeInfo');pairs,issues=universe(list(byaddr.values()),exchange,chain='56')
        matched={p['token']['contractAddress'].lower() for p in pairs}
        quarantine={a for issue in issues for a in issue.get('alpha_ids',[])}
        future_names={str(f.get('baseAsset','')).upper() for f in exchange['symbols'] if f.get('contractType')=='PERPETUAL' and f.get('quoteAsset')=='USDT' and f.get('status')=='TRADING'}
    except Exception:
        matched=set();quarantine={t['alphaId'] for t in byaddr.values()};future_names=set();errors.append({'stage':'futures_directory','error':'unavailable'})
    with store.db() as db:
        states={r['address']:json.loads(r['payload']) for r in db.execute('SELECT * FROM strategy_state')}
        positions=open_positions(db)
        papers=list(db.execute('SELECT * FROM paper_tracks WHERE created>?',(now-8*86400,)))
        evaluation_addresses=[r[0] for r in db.execute('SELECT DISTINCT address FROM evaluation_tracks WHERE opened>?',(now-181*86400,))]
    quarantine.update(t['alphaId'] for a,t in byaddr.items() if a not in matched and ({str(t.get('symbol','')).upper(),str(t.get('cexCoinName','')).upper()} & future_names))
    eligible=[]
    for address,t in byaddr.items():
        if t['alphaId'] in quarantine:continue
        if address in matched and address not in duals:continue
        if not gates(t,duals.get(address)):eligible.append(address)
    # A rotating majority prevents permanent starvation; hot slots improve responsiveness.
    rotation=sorted(eligible,key=lambda a:(states.get(a,{}).get('checked',0),a))
    budget=args.hourly_enrich
    selected=rotation[:max(1,budget*2//3)]
    hot=sorted(eligible,key=lambda a:(-(num(byaddr[a].get('percentChange24h')) or 0),a))
    for a in hot:
        if len(selected)>=budget:break
        if a not in selected:selected.append(a)
    # Active setups are revisited even when they fail a new gate; bounded by signal slots.
    setups=[a for a,s in states.items() if s.get('setup_end') and now-s.get('setup_seen',0)<86400 and not s.get('invalid') and a in byaddr]
    selected=list(dict.fromkeys(setups+selected))[:budget+24]
    position_addresses={p['address'] for p in positions}
    required={p['address']:byaddr.get(p['address'],{}).get('alphaId',p['alpha_id']) for p in positions}
    required.update({p['address']:byaddr[p['address']]['alphaId'] for p in papers if p['address'] in byaddr})
    required.update({a:byaddr[a]['alphaId'] for a in selected})
    attempts=store.state('evaluation_quote_attempts',{})
    for address in sorted((a for a in evaluation_addresses if a in byaddr),key=lambda a:attempts.get(a,0))[:40]:
        required[address]=byaddr[address]['alphaId'];attempts[address]=now
    store.put('evaluation_quote_attempts',attempts)
    def fetch(item):
        address,alpha=item
        q,diagnostic=get_quote(api.data,alpha,fallback=address in position_addresses)
        if not q:return address,None,None,'quote_missing_or_stale',diagnostic
        fx=None;error=None
        if address in selected:
            st=states.get(address,{});target=int(time.time()*1000)//HOUR*HOUR-1
            if (st.get('features') or {}).get('end')==target:fx=st['features']
            else:
                try:fx=hourly_features(candles(api.data(ALPHA+'/klines',{'symbol':alpha+'USDT','interval':'1h','limit':60})),time.time())
                except Exception:error='hourly_data_unavailable'
        return address,q,fx,error,diagnostic
    results={};diagnostics={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for address,q,fx,error,diagnostic in pool.map(fetch,required.items()):
            results[address]=(q,fx)
            diagnostics[address]=diagnostic
            if error:
                target=skipped if error=='quote_missing_or_stale' and address not in {p['address'] for p in positions} else errors
                target.append({'address':address,'error':error})
    # Only fetch risk data for a possible alert or a registered position; bound provider load.
    risk_addresses=set(p['address'] for p in positions)
    for address,(q,fx) in results.items():
        if address in selected and q and (address in setups or (fx and fx['volume_ratio']>=2 and fx['close']>=fx['level']*.95)):
            risk_addresses.add(address)
    risks={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for address,risk in zip(sorted(risk_addresses),pool.map(lambda a:inspect(a,base/'safety'),sorted(risk_addresses))):
            risks[address]=risk
            risks[address]['unlock']=unlock_override(address,base)
            risks[address]['entities']=entity_evidence(address,base)
    alerts=[];observations=[];now=time.time()
    with store.db() as db:
        # Failed/stale attempts also advance the rotation, otherwise illiquid tokens starve the rest.
        for address in selected:
            row=db.execute('SELECT payload FROM strategy_state WHERE address=?',(address,)).fetchone()
            state=json.loads(row[0]) if row else {};state['checked']=now
            db.execute('INSERT OR REPLACE INTO strategy_state VALUES(?,?)',(address,json.dumps(state)))
        for p in positions:
            p=db.execute('SELECT * FROM positions WHERE id=? AND closed IS NULL',(p['id'],)).fetchone()
            if p is None:continue
            q=(results.get(p['address']) or (None,None))[0]
            if valid(q,now):position_tick(db,p,q,num(byaddr.get(p['address'],{}).get('liquidity')),now)
            else:
                detail=dict(diagnostics.get(p['address'],{}))
                if q:detail['reason']='quote_expired_during_scan'
                missing_position(db,p,now,detail)
            risk=risks.get(p['address'],{})
            if risk.get('flags') or risk.get('unlock',{}).get('large_unlock_soon'):
                emit(db,f'position-security:{p["id"]}:{int(now//86400)}',f'⚠️ 持仓 #{p["id"]} {p["symbol"]} 链上/解锁风险\n'+('；'.join(risk.get('flags',[])) or '已录入披露显示7日内解锁≥当前流通量5%')+'\n第三方/人工披露需复核，不执行卖出。',now)
        for address,(q,fx) in results.items():
            if not q:continue
            if now*1000-q['time']>180000:
                errors.append({'address':address,'error':'quote_expired_during_scan'});continue
            if fx and fx['end']!=int(now*1000)//HOUR*HOUR-1:fx=None
            paper_tick(db,address,q,now)
            evaluation.observe(db,address,q,now)
            if address not in selected:continue
            t=byaddr[address];blocked=gates(t,duals.get(address))
            if t['alphaId'] in quarantine or (address in matched and address not in duals):blocked.append('合约身份或本轮行情未核实')
            risk=risks.get(address,{'status':'not_checked','flags':[]})
            blocked.extend(risk.get('flags',[]))
            if risk.get('unlock',{}).get('large_unlock_soon'):blocked.append('已录入披露显示7日内解锁≥流通量5%')
            if ratio(q['price'],t.get('price')) is None or not .8<=ratio(q['price'],t.get('price'))<=1.2:blocked.append('名录与实时报价偏差超过20%')
            market='Alpha＋USDT永续（符号/报价匹配，非合约地址级证明）' if address in matched else '仅Alpha观察池（未匹配到交易中的USDT永续）'
            row=db.execute('SELECT payload FROM strategy_state WHERE address=?',(address,)).fetchone()
            current=json.loads(row[0]) if row else {}
            new,events=transition(current,fx,q,blocked,now)
            new.update(checked=now,features=fx)
            db.execute('INSERT OR REPLACE INTO strategy_state VALUES(?,?)',(address,json.dumps(new)))
            observations.append({'address':address,'symbol':t['symbol'],'market':market,'features':fx,'quote':q,'blocks':blocked,'setup':new.get('setup_end'),'invalid':new.get('invalid',False),'safety':risk,'token':{k:t.get(k) for k in ('marketCap','fdv','liquidity')},'crypto_market_rank':t.get('crypto_market_rank'),'dual_matched':address in matched,'setup_seen':new.get('setup_seen'),'retested':new.get('retested',False)})
            for kind,end,level,reason in events:
                key=f'{VERSION}:{address}:{kind}:{end}'
                payload={'kind':kind,'address':address,'symbol':t['symbol'],'quote':q,'features':fx,'level':level,'market':market,'rule_version':VERSION,'as_of':utc(),'safety':risk}
                inserted=db.execute('INSERT OR IGNORE INTO strategy_events VALUES(?,?,?)',(key,now,json.dumps(payload))).rowcount
                if not inserted:continue
                text=render(kind,t,market,fx,q,level,reason,blocked,duals.get(address),now)
                share=risk.get('raw_top10_share')
                text+='\n安全快照：'+risk.get('status','unknown')+'；第三方来源 GoPlus'
                text+='\n前十原始占比：'+(f'{share:.1%}（未剔除池/桥/交易所，不能推断控盘）' if share is not None else '未知')
                text+='\n解锁资料：'+risk.get('unlock',{}).get('status','unknown')
                claims=risk.get('entities',[]);labels=risk.get('entity_labels',[])
                text+='\n机构关系：'+('；'.join(c['name']+' / '+c['role']+' 来源 '+c['source'] for c in claims[:2]) if claims else '暂无可核验归属记录')
                if labels:text+='\n供应商钱包标签：'+'；'.join(str(x.get('label')) for x in labels[:2])
                text+='\n投资方/做市商/钱包标签均不证明操盘；归属信息不加买入分。'
                if kind in ('breakout','retest'):
                    evaluation.register(db,key,address,t['symbol'],q,kind,{'features':fx,'market':market,'safety':risk,'token_valuation':{k:t.get(k) for k in ('marketCap','fdv','liquidity')}},now)
                    text=reference_position(db,t,q,key,now)+'\n\n'+text
                if kind=='invalidated':
                    from alerts import raise_alert
                    raise_alert(db,'setup:'+address+':'+str(end),text,now)
                else:emit(db,key,text,now)
                alerts.append(payload)
                if kind in ('breakout','retest') and db.execute('SELECT count(*) FROM paper_tracks WHERE created>?',(now-8*86400,)).fetchone()[0]<20:
                    price=q['price'];db.execute('INSERT OR IGNORE INTO paper_tracks(key,address,symbol,created,entry,last,peak,trough,checked) VALUES(?,?,?,?,?,?,?,?,?)',(key,address,t['symbol'],now,price,price,price,price,now))
    with store.db() as db:evaluation.controls(db,observations,byaddr,now)
    report={'version':VERSION,'as_of':utc(),'status':'partial' if errors else 'ok','universe_count':len(byaddr),'eligible_count':len(eligible),'checked':len(selected),'quotes_checked':len(required),'positions':len(positions),'skipped':skipped,'errors':errors,'crypto_market_rank':rank_health,'alerts':alerts,'observations':observations,
            'notice':'新增小时规则未经样本外验证；行情为采样，不保证即时执行或百倍收益。'}
    dump(base/'strategy-latest.json',report)
    with store.db() as db:
        for p in db.execute('SELECT * FROM paper_tracks').fetchall():
            marks=json.loads(p['marks'])
            for h in (24,72,168):
                if now-p['created']>h*3600+1800 and str(h) not in marks:marks[str(h)]={'missing':True}
            db.execute('UPDATE paper_tracks SET marks=? WHERE key=?',(json.dumps(marks),p['key']))
        paper=[dict(r) for r in db.execute('SELECT * FROM paper_tracks ORDER BY created DESC')]
    dump(base/'paper-report.json',{'as_of':utc(),'tracks':paper,'notice':'前瞻纸面参考报价，非成交；峰谷仅采样值；未扣费用/滑点；不是回测胜率。'})
    return report
