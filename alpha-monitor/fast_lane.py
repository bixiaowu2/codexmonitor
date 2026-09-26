#!/usr/bin/env python3
"""Independent 60-second lane: costs/exits, support breaks, 15m heads-up, listing changes."""
import argparse,concurrent.futures,json,subprocess,time
from pathlib import Path
from urllib.parse import urlencode
import radar,strategy
from cloud import Store
from positions import initialize,open_positions,position_tick,missing_position,emit,VERSION
from alerts import raise_alert,resolve

QUARTER=900000

def fetch(url,params=None):
    full=url+('?' + urlencode(params) if params else '')
    r=subprocess.run(['curl','--compressed','--fail','--silent','--show-error','--connect-timeout','4','--max-time','8',full],capture_output=True,timeout=10)
    if r.returncode:raise ValueError('public_data_unavailable')
    d=json.loads(r.stdout)
    if isinstance(d,dict) and (d.get('success') is False or ('code' in d and d['code'] not in (0,'000000',1))):raise ValueError('public_data_rejected')
    return d.get('data',d) if isinstance(d,dict) else d

def quarter_signal(bars,now):
    if len(bars)<17:return None
    b=bars[-17:];latest=b[-1];end=int(now*1000)//QUARTER*QUARTER-1
    if latest['end']!=end or any(y['t']-x['t']!=QUARTER for x,y in zip(b,b[1:])):return None
    import statistics
    med=statistics.median(x['q'] for x in b[:-1]);level=max(x['h'] for x in b[:-1]);change=latest['c']/latest['o']-1
    if latest['q']>=5000 and latest['q']/med>=3 and .005<=change<=.1 and level*.99<=latest['c']<=level*1.08:
        return {'end':end,'level':level,'close':latest['c'],'volume_ratio':latest['q']/med,'change':change}
    return None

def directory(store,now):
    cached=store.state('fast_directory',{})
    if 0<=now-cached.get('observed',0)<120:return cached,[]
    errors=[]
    try:
        tokens=fetch(radar.TOKENS)
        if not isinstance(tokens,list) or not tokens:raise ValueError()
        active={t['contractAddress'].lower():t for t in tokens if str(t.get('chainId'))=='56' and t.get('alphaId') and t.get('contractAddress') and not any(t.get(k) for k in ('offline','offsell','fullyDelisted'))}
        if not active:raise ValueError()
    except Exception:return cached,['alpha_directory_unavailable']
    current={'observed':now,'tokens':active}
    try:
        futures=fetch(radar.FUTURES+'/fapi/v1/exchangeInfo');pairs,_=radar.universe(list(active.values()),futures,chain='56')
        current['dual']={p['token']['contractAddress'].lower():p['future']['symbol'] for p in pairs}
    except Exception:errors.append('futures_directory_unavailable')
    with store.db() as db:
        previous=store.state('listing_baseline',{})
        if previous:
            added=set(active)-set(previous.get('alpha',[]))
            new_dual=set(current.get('dual',{}))-set(previous.get('dual',[])) if 'dual' in current and 'dual' in previous else set()
            for address in sorted(added|new_dual):
                t=active[address];kind='新增Alpha名录项目' if address in added else '新发现Alpha＋USDT永续交集'
                emit(db,f'listing-v3:{address}:{kind}:{int(now//86400)}',f'🔎 {kind} · {t["symbol"]}\n地址 {address}\n这是名录变化，不等于准确上市时刻或买入信号；未建立假定买入仓。',now)
        baseline={'alpha':list(active)}
        if 'dual' in current:baseline['dual']=list(current['dual'])
        elif 'dual' in previous:baseline['dual']=previous['dual']
        db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',('listing_baseline',json.dumps(baseline)))
        db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',('fast_directory',json.dumps(current)))
    return current,errors

def run(store):
    start=time.time()
    with store.db() as db:
        initialize(db)
        import evaluation
        evaluation.initialize(db)
    catalog,errors=directory(store,start);tokens=catalog.get('tokens',{})
    if start-catalog.get('observed',0)>600:tokens={}
    with store.db() as db:
        positions=open_positions(db)
        states={r['address']:json.loads(r['payload']) for r in db.execute('SELECT * FROM strategy_state')}
    setups=[a for a,s in states.items() if s.get('setup_end') and not s.get('invalid') and start-s.get('setup_seen',0)<86400 and a in tokens]
    setups=sorted(setups,key=lambda a:states[a].get('fast_checked',0))[:24]
    rotation=store.state('quarter_checked',{})
    eligible=[a for a,t in tokens.items() if not strategy.gates(t)]
    selected=sorted(eligible,key=lambda a:(rotation.get(a,0),a))[:8]
    for a in sorted(eligible,key=lambda a:-(radar.num(tokens[a].get('percentChange24h')) or 0)):
        if len(selected)>=12:break
        if a not in selected:selected.append(a)
    ids={p['address']:p['alpha_id'] for p in positions}
    ids.update({a:tokens[a]['alphaId'] for a in setups+selected})
    def observe(item):
        address,alpha=item;q=None;fx=None;error=None
        try:q=strategy.fresh_quote(fetch(radar.ALPHA+'/ticker',{'symbol':alpha+'USDT'}),time.time())
        except Exception:return address,q,fx,'quote_unavailable_or_stale'
        if address in selected:
            try:fx=quarter_signal(radar.candles(fetch(radar.ALPHA+'/klines',{'symbol':alpha+'USDT','interval':'15m','limit':20})),time.time())
            except Exception:error='quarter_data_unavailable'
        return address,q,fx,error
    samples={};skipped=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for address,q,fx,error in pool.map(observe,ids.items()):
            samples[address]=(q,fx)
            if error:skipped.append({'address':address,'error':error})
    now=time.time();alerts=0
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        for old in positions:
            p=db.execute('SELECT * FROM positions WHERE id=? AND closed IS NULL',(old['id'],)).fetchone()
            if p is None:continue
            q=samples.get(p['address'],(None,None))[0]
            if q and now*1000-q['time']<=180000:
                position_tick(db,p,q,radar.num(tokens.get(p['address'],{}).get('liquidity')),now)
            else:
                missing_position(db,p,now);errors.append('position_quote_unavailable')
        for address,(q,fx) in samples.items():
            if q:evaluation.observe(db,address,q,now)
        for address in setups:
            row=db.execute('SELECT payload FROM strategy_state WHERE address=?',(address,)).fetchone()
            s=json.loads(row[0]);s['fast_checked']=now
            q=samples.get(address,(None,None))[0]
            if q and now*1000-q['time']<=180000 and not s.get('invalid') and q['price']<s['level']*.95:
                s['invalid']=True
                raise_alert(db,'setup:'+address+':'+str(s['setup_end']),f'⚠️ 突破支撑失效 · {tokens[address]["symbol"]}\n最新报价 {q["price"]:.8g}，已低于支撑5%线 {s["level"]*.95:.8g}。\n地址 {address}\n报价UTC {radar.utc(q["time"])}\n这不是成交或自动卖出。',now)
                alerts+=1
            db.execute('UPDATE strategy_state SET payload=? WHERE address=?',(json.dumps(s),address))
        for address in selected:
            rotation[address]=now
            q,fx=samples.get(address,(None,None))
            if q and fx and now*1000-q['time']<=180000 and fx['end']==int(now*1000)//QUARTER*QUARTER-1 and q['price']<=fx['close']*1.03:
                emit(db,f'quarter-watch:{address}:{fx["end"]}',f'👀 15分钟异动预警 · {tokens[address]["symbol"]}\n量比 {fx["volume_ratio"]:.2f}；15分钟涨幅 {fx["change"]:+.1%}\n最新参考报价 {q["price"]:.8g} USDT\n地址 {address}\n尚未得到小时突破确认，安全与机构归属未核实；不是买入参考，未建立假定买入仓。',now)
                alerts+=1
        db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',('quarter_checked',json.dumps({a:t for a,t in rotation.items() if a in tokens})))
    return {'as_of':radar.utc(),'version':VERSION,'status':'partial' if errors else 'ok','errors':sorted(set(errors)),'skipped':len(skipped),'positions_checked':len(positions),'setups_checked':len(setups),'quarter_checked':len(selected),'directory_size':len(tokens),'directory_observed':catalog.get('observed'),'alerts':alerts,'elapsed':round(time.time()-start,2)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);args=p.parse_args();store=Store(args.data)
    try:result=run(store)
    except Exception as e:result={'as_of':radar.utc(),'status':'failed','error':type(e).__name__}
    radar.dump(Path(args.data)/'fast-health.json',result);print(json.dumps(result),flush=True)
    if result['status']=='failed':raise SystemExit(1)

if __name__=='__main__':main()
