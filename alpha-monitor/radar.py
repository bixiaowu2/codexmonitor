#!/usr/bin/env python3
"""Public-data Binance Alpha radar. Python 3.10+, curl. No account or orders."""
from __future__ import annotations
import argparse, collections, concurrent.futures, csv, hashlib, html, json, math, os, sqlite3, statistics, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

ALPHA='https://www.binance.com/bapi/defi/v1/public/alpha-trade'
TOKENS='https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list'
FUTURES='https://fapi.binance.com'
DAY=86400000
FOCUS=['AKE','BTW','M','UB','ZAMA','PIEVERSE','RIVER']

def utc(ms=None):
    return datetime.fromtimestamp((ms if ms is not None else time.time()*1000)/1000,timezone.utc).isoformat(timespec='seconds')

def num(x):
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None

def ratio(a,b):
    a,b=num(a),num(b)
    return a/b if a is not None and b is not None and b>0 else None

def dump(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(path)

class PublicAPI:
    def __init__(self,folder,ttl=0):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.ttl=ttl
    def get(self,url,params=None):
        full=url+('?' + urlencode(params) if params else '')
        key=hashlib.sha256(full.encode()).hexdigest();path=self.folder/(key+'.json')
        if path.exists() and self.ttl and time.time()-path.stat().st_mtime<self.ttl:
            return json.loads(path.read_text())['payload']
        # curl honors HTTPS_PROXY / ALL_PROXY and verifies certificates. No -k.
        for attempt in range(3):
            p=subprocess.run(['curl','--compressed','--fail-with-body','--silent','--show-error','--connect-timeout','10','--max-time','30',full],capture_output=True,timeout=35)
            if p.returncode==0:
                try:
                    payload=json.loads(p.stdout)
                    if isinstance(payload,dict) and (payload.get('success') is False or ('code' in payload and payload['code'] not in (0,'000000',1))):
                        raise RuntimeError(full+f': API code {payload.get("code")}: {payload.get("message")}')
                    dump(path,{'url':full,'retrieved_at':utc(),'sha256':hashlib.sha256(p.stdout).hexdigest(),'payload':payload})
                    return payload
                except (ValueError,TypeError) as e:err=str(e)
            else:err=f'curl={p.returncode} '+p.stderr.decode(errors='replace')[:180]
            if attempt<2:time.sleep(2**attempt)
        raise RuntimeError(full+': '+err)
    def data(self,url,params=None):
        p=self.get(url,params);return p.get('data',p) if isinstance(p,dict) else p

def universe(tokens,futures,chain='56',active=True):
    """Exact symbols/official cex alias only; ambiguous identities quarantined."""
    if not isinstance(tokens,list) or not tokens or not isinstance(futures,dict) or not isinstance(futures.get('symbols'),list) or not futures['symbols']:
        raise ValueError('Unexpected token/exchange schema')
    bybase=collections.defaultdict(list)
    for f in futures['symbols']:
        if f.get('contractType')=='PERPETUAL' and f.get('quoteAsset')=='USDT' and (not active or f.get('status')=='TRADING'):
            bybase[f.get('baseAsset','').upper()].append(f)
    grouped=collections.defaultdict(list)
    for t in tokens:
        if chain!='all' and str(t.get('chainId'))!=chain:continue
        if active and any(t.get(k) for k in ['offline','offsell','fullyDelisted']):continue
        # Use aliases declared by Binance, never guess numeric prefixes.
        names={str(t.get('symbol','')).upper(),str(t.get('cexCoinName','')).upper()}-{''}
        matches={f['symbol']:f for name in names for f in bybase.get(name,[])}
        if len(matches)!=1:continue
        f=next(iter(matches.values()))
        grouped[f['symbol']].append((t,f))
    out=[];issues=[]
    for symbol,pairs in grouped.items():
        addresses={(str(t.get('chainId')),t.get('contractAddress','').lower() if t.get('contractAddress','').startswith('0x') else t.get('contractAddress','')) for t,f in pairs}
        if len(addresses)!=1:
            issues.append({'futures_symbol':symbol,'reason':'多个 Alpha 合约地址对应相同符号，隔离待核实','alpha_ids':[t.get('alphaId') for t,f in pairs]});continue
        t,f=max(pairs,key=lambda p:p[0].get('listingTime',0))
        if not t.get('alphaId') or not t.get('contractAddress'):continue
        out.append({'token':t,'future':f,'identity':'official_alias_or_unique_symbol_price_check_pending'})
    return out,issues

def candles(raw,now_ms=None):
    now_ms=now_ms or int(time.time()*1000)
    if not isinstance(raw,list):raise ValueError('Klines must be a list')
    out={}
    for r in raw:
        if not isinstance(r,list) or len(r)<9:continue
        vals=[num(r[i]) for i in [0,1,2,3,4,6,7,8]]
        if any(v is None for v in vals):continue
        ts,o,h,l,c,end,q,n=vals
        if end>=now_ms or min(o,h,l,c)<=0 or q<=0 or n<1:continue
        if not l<=min(o,c)<=max(o,c)<=h:continue
        out[int(ts)]={'t':int(ts),'o':o,'h':h,'l':l,'c':c,'q':q,'n':n,'end':int(end)}
    return [out[t] for t in sorted(out)]

def features_at(bars,i):
    if i<30:return None
    window=bars[i-30:i+1]
    if any(b['t']-a['t']!=DAY for a,b in zip(window,window[1:])):return None
    a=bars[i];prior=bars[i-30:i];med=statistics.median(b['q'] for b in prior)
    return {'volume_ratio':ratio(a['q'],med),'return_7d':a['c']/bars[i-7]['c']-1,
            'breakout_ratio':a['c']/max(b['h'] for b in prior),
            'drawdown_30d':1-a['c']/max(b['h'] for b in window),'daily_quote_volume':a['q']}

def early_signal(f):
    return bool(f and f['volume_ratio'] is not None and f['volume_ratio']>=2 and
                f['breakout_ratio']>=1 and .0<=f['return_7d']<=1.0 and f['daily_quote_volume']>=100000)

def outcomes(bars,anchor,horizon=90):
    b=[x for x in bars if x['t']>=anchor]
    if not b:return {'error':'没有起点后的有效已收盘日线'}
    entry=b[0];p=entry['o'];future=[x for x in b if x['t']<entry['t']+horizon*DAY]
    complete=(len(future)==horizon and all(y['t']-x['t']==DAY for x,y in zip(future,future[1:])))
    peak=max(x['h'] for x in b);peakbar=max(b,key=lambda x:x['h']);running=p;mdd=0;max_up=1;low_before=p
    # Chronological close-to-future-high excursion: no same-candle low/high ordering assumption.
    for x in b:
        max_up=max(max_up,x['h']/low_before);low_before=min(low_before,x['c'])
        running=max(running,x['c']);mdd=max(mdd,1-x['c']/running)
    return {'entry_day':utc(entry['t']),'entry_price':p,'coverage_delay_days':round((entry['t']-anchor)/DAY,2),
            'bars':len(b),'last_closed_day':utc(b[-1]['end']),'peak_multiple':peak/p,'peak_day':utc(peakbar['t']),'current_multiple':b[-1]['c']/p,
            'current_vs_peak':b[-1]['c']/peak-1,'max_close_drawdown':mdd,'chronological_low_close_to_later_high':max_up,
            '90d_complete':complete,'90d_peak_multiple':max(x['h'] for x in future)/p if complete else None,
            '90d_close_return':future[-1]['c']/p-1 if complete else None,
            '90d_worst_low_return':min(x['l'] for x in future)/p-1 if complete else None}

def study_one(api,pair):
    t,f=pair['token'],pair['future'];start=int(t.get('listingTime') or 0)
    source_url=ALPHA+'/klines';params={'symbol':t['alphaId']+'USDT','interval':'1d','startTime':start,'limit':1500}
    market='alpha_spot';alpha_error=None
    try:
        raw=api.data(source_url,params)
        if not raw:raise RuntimeError('No records found')
    except RuntimeError as exc:
        if not any(s in str(exc) for s in ['No records found','Invalid symbol']):raise
        # A futures price is a clearly labelled proxy, never a fabricated Alpha price.
        alpha_error=str(exc);market='futures_proxy';source_url=FUTURES+'/fapi/v1/klines'
        params={'symbol':f['symbol'],'interval':'1d','startTime':max(start,int(f['onboardDate'])),'limit':1500}
        raw=api.data(source_url,params)
    bars=candles(raw);dual=max(start,int(f.get('onboardDate') or 0));after=[b for b in bars if b['t']>=dual]
    # Compare all assets at day 30 after dual listing, not at their chosen historical bottom.
    fixed=None
    if len(after)>31:
        fx=features_at(after,30);out=outcomes(after,after[31]['t'])
        if fx and out.get('90d_complete'):fixed={'features':fx,'outcome':out}
    signal=None
    for i in range(30,len(after)-1):
        fx=features_at(after,i)
        if early_signal(fx):
            signal={'signal_day':utc(after[i]['end']),'features':fx,'outcome':outcomes(after,after[i+1]['t'])};break
    return {'symbol':t['symbol'],'name':t['name'],'alpha_id':t['alphaId'],'address':t['contractAddress'],
            'alpha_listing':utc(start),'futures_listing':utc(f['onboardDate']),'futures_symbol':f['symbol'],
            'listing_order':'合约先于Alpha' if f['onboardDate']<start else 'Alpha先于合约',
            'after_alpha':outcomes(bars,start) if market=='alpha_spot' else {'error':'Alpha历史缺失，未替代'},'price_market':market,'alpha_history_error':alpha_error,'after_both':outcomes(bars,dual),'fixed_day30':fixed,'first_signal':signal,
            'current_market_cap':num(t.get('marketCap')),'current_fdv':num(t.get('fdv')),
            'current_liquidity':num(t.get('liquidity')),'current_holders':num(t.get('holders')),
            'source_url':source_url+'?'+urlencode(params)}

def research(args):
    base=Path(args.data);api=PublicAPI(base/'raw',ttl=86400)
    tokens=api.data(TOKENS);exchange=api.data(FUTURES+'/fapi/v1/exchangeInfo')
    pairs,issues=universe(tokens,exchange,active=False)
    if args.limit:pairs=sorted(pairs,key=lambda p:(p['token']['symbol'].upper() not in FOCUS,p['token']['alphaId']))[:args.limit]
    results=[];errors=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        tasks={pool.submit(study_one,api,p):p for p in pairs}
        for task in concurrent.futures.as_completed(tasks):
            p=tasks[task]
            try:results.append(task.result());print('research',len(results),'/',len(pairs),p['token']['symbol'],flush=True)
            except Exception as e:errors.append({'symbol':p['token']['symbol'],'error':str(e)});print('research error',p['token']['symbol'],str(e)[:130],flush=True)
    results.sort(key=lambda r:r['symbol'].upper())
    report={'as_of':utc(),'method':'Closed daily Alpha USDT candles; explicitly labelled futures proxy when Alpha history unavailable. First full day after current metadata intersection; no fees/slippage model',
            'scope':'Current official Alpha directory joined to available USD-M perpetual metadata including non-TRADING; not a reconstructed historical universe',
            'tokens_in_directory':len(tokens),'cohort_size':len(pairs),'results':results,'identity_quarantine':issues,'errors':errors}
    dump(base/'research.json',report);write_research(base,report);return report

def fmt(n,digits=2):return '缺失' if n is None else f'{n:,.{digits}f}'

def write_research(base,r):
    rows=r['results'];focus={x['symbol'].upper():x for x in rows}
    lines=['# BSC Alpha 与合约：历史涨幅研究',f'抓取时间（UTC）：{r["as_of"]}',
           '## 口径与证据边界',
           '数据来自 Binance Alpha 官方名录、USD-M 合约元数据及 Alpha USDT 日线。Alpha接口无记录时，双条件后指标采用单独标明的永续合约价格代理，不填补Alpha现货历史；前置信号分组只含Alpha现货。按链和合约地址识别 Alpha 资产；与合约仅能做唯一符号/官方别名连接，不把同符号多地址的资产自动合并。',
           '以下“上线后”均指当前API元数据基准，并非已逐项核验的首次上市事件。起点为 Alpha listingTime 和合约 onboardDate 的较晚者，再取其后首根有效、已收盘日线的开盘价。它是可复现的价格基准，不是假定能成交的回测。listingTime 可能随重上架变化，onboardDate 也未逐条用公告验证。',
           '历史最高价/起点价包含盘中尖峰；另列期末收益与最大收盘回撤。日线不证明能按最高价卖出，也不能还原历史池深度。先前低收盘价至后续高价的最大涨幅是事后描述，不是可交易策略。',
           '样本来自当前名录，包含可获取的非交易合约，但仍有幸存者偏差；不代表所有历史 Alpha。未从上涨后市值或持仓倒推上涨前特征。未验证持仓集中、关联钱包、解锁、真实买盘、因果关系。',
           f'交集 {r["cohort_size"]} 个，获得日线 {len(rows)} 个，失败 {len(r["errors"])} 个，歧义隔离 {len(r["identity_quarantine"])} 项。',
           '## 指定项目',
           '|项目/价格来源|Alpha元数据日期 UTC|合约元数据日期 UTC|Alpha后峰值倍数|双条件后峰值倍数|双条件后期末倍数|最大收盘回撤|',
           '|---|---|---|---:|---:|---:|---:|']
    for s in FOCUS:
        x=focus.get(s)
        if not x:lines.append(f'|{s}|数据缺失||||||');continue
        a=x['after_alpha'];b=x['after_both']
        lines.append(f'|{s}{"（合约代理）" if x.get("price_market")=="futures_proxy" else ""}|{x["alpha_listing"][:10]}|{x["futures_listing"][:10]}|{fmt(a.get("peak_multiple"))}x|{fmt(b.get("peak_multiple"))}x|{fmt(b.get("current_multiple"))}x|{fmt(100*b["max_close_drawdown"]) if "max_close_drawdown" in b else "缺失"}%|')
    lines+=['## 其他项目与对照','以下按双条件成立后的历史峰值倍数排序，并保留表现差的样本；这不是当前买入名单。',
            '|项目|峰值倍数|期末倍数|90日完整|起点数据延迟天数|','|---|---:|---:|---|---:|']
    valid=[x for x in rows if 'peak_multiple' in x['after_both']]
    ordered=sorted(valid,key=lambda x:x['after_both']['peak_multiple'],reverse=True)
    chosen=ordered[:15]+[x for x in sorted(valid,key=lambda x:x['after_both']['current_multiple'])[:10] if x not in ordered[:15]]
    for x in chosen:
        b=x['after_both'];lines.append(f'|{x["symbol"]}{"（合约代理）" if x.get("price_market")=="futures_proxy" else ""}|{fmt(b["peak_multiple"])}x|{fmt(b["current_multiple"])}x|{b["90d_complete"]}|{b["coverage_delay_days"]}|')
    lines+=['## 可观察的前置信号检验',
            '在每个代币双条件成立后的第31根连续日线收盘计算特征，下一日开盘为统一参考价，只比较后续90日完整的样本。先固定时间，再观察结果；不挑事后底部。成功标签暂定义90日盘中触及5倍，属于探索性分析。',
            '|分组|样本数|放量倍数中位数|7日涨幅中位数|距过去30日最高价比率中位数|','|---|---:|---:|---:|---:|']
    fixed=[x['fixed_day30'] for x in rows if x.get('price_market','alpha_spot')=='alpha_spot' and x['fixed_day30']]
    for label,group in [('90日峰值≥5倍',[x for x in fixed if x['outcome']['90d_peak_multiple']>=5]),('90日峰值<5倍',[x for x in fixed if x['outcome']['90d_peak_multiple']<5])]:
        med=lambda key:statistics.median(x['features'][key] for x in group) if group else None
        lines.append(f'|{label}|{len(group)}|{fmt(med("volume_ratio"))}|{fmt(med("return_7d"))}|{fmt(med("breakout_ratio"))}|')
    signals=[x['first_signal'] for x in rows if x.get('price_market','alpha_spot')=='alpha_spot' and x['first_signal'] and x['first_signal']['outcome'].get('90d_complete')]
    wins=sum(x['outcome']['90d_peak_multiple']>=5 for x in signals)
    lines+=['### 固定规则事件检查',
            '假设规则：连续30日历史、当日成交额≥10万USDT且≥前30日中位数2倍、收盘突破前30日最高价、7日涨幅0–100%。每天只使用当时已收盘数据，第一次触发后下一日开盘观察90日。',
            f'完整90日事件 {len(signals)} 个，其中盘中触及5倍 {wins} 个。未进行持仓/卖出模拟、费用/滑点扣除或独立留出验证；这个比例不是100倍概率，也不是策略胜率。',
            '## 监控指导：待验证的假设',
            '1. 上线顺序和等待时间应分别记录；以双条件成立后做观察，避免把合约上线前涨幅归给本策略。',
            '2. 分别监控低位放量、突破、回踩，不直接奖励已暴涨幅度；高波动本身不是优势。放量可能来自刷量或积分交易，需池深度与独立买家验证。',
            '3. 同时看流通市值和FDV；100倍价格意味着约100倍当前流通市值（供应不变假设），解锁会进一步提高所需市值。',
            '4. 流动性、持有人增长、持仓集中和LP控制权是不同指标；持有人数量不能替代去除池/桥/交易所后的集中度。缺失数据必须标未知。',
            '5. 持仓量上升、价格上涨、资金费率变化要联合解释，不能据负费率断定会逼空，也不能据合约上线断定会拉盘。',
            '6. 先积累前瞻快照与纸面触发记录，再做按时间划分的训练/验证；未校准前只显示观察分，不输出收益概率。',
            '## 原始数据与复现',
            '`raw/*.json` 保存请求URL、抓取时间、响应哈希及原始payload；`research.json` 保存全部项目与错误。`research.csv` 可用于独立分析。',
            f'- [Alpha名录]({TOKENS})',f'- [合约元数据]({FUTURES}/fapi/v1/exchangeInfo)',
            '- [Alpha K线文档](https://developers.binance.com/docs/alpha/market-data/rest-api/klines)',
            '- [USD-M 合约信息文档](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)']
    # Quantified conclusions distinguish descriptive outcomes from prospective evidence.
    source_counts=collections.Counter(x.get('price_market','alpha_spot') for x in rows)
    checks_path=base/'listing-date-check.json'
    checks=json.loads(checks_path.read_text()) if checks_path.exists() else []
    checks_bad=[x for x in checks if x.get('predates_listing_metadata')]
    def fact(symbol,period,key,multiplier=1):
        value=focus.get(symbol,{}).get(period,{}).get(key)
        return fmt(value*multiplier if value is not None else None)
    ake_signal=(focus.get('AKE',{}).get('first_signal') or {}).get('outcome',{})
    ake_90=ake_signal.get('90d_close_return')
    conclusions=['## 本轮数据实际支持什么',
                 f'全量覆盖 {len(rows)} 个项目：Alpha现货日线 {source_counts.get("alpha_spot",0)} 个，合约价格代理 {source_counts.get("futures_proxy",0)} 个。代理组不混入前置信号统计。',
                 '1. 指定案例的共同点是“曾出现可观上涨”，不是“在任意入场点都能获得100倍”。同一代币的倍数随入场时间差异很大。',
                 f'2. 等到合约条件成立，可能已错过早期价格。AKE与BTW的Alpha基准峰值倍数分别为{fact("AKE","after_alpha","peak_multiple")}与{fact("BTW","after_alpha","peak_multiple")}，但双条件元数据基准下分别为{fact("AKE","after_both","peak_multiple")}与{fact("BTW","after_both","peak_multiple")}。',
                 f'3. 大涨与极深回撤可以共存。以本报告双条件基准，RIVER最大收盘回撤为{fact("RIVER","after_both","max_close_drawdown",100)}%；LAB曾达到{fact("LAB","after_both","peak_multiple")}倍的盘中峰值，但期末为{fact("LAB","after_both","current_multiple")}倍。退出管理必须单独验证。',
                 '4. 当前的市值、FDV与池深度不能证明上涨前也如此；因此目前没有证据把“低流通、控盘、负费率、聪明钱买入”称为这些项目已验证的共同前因。',
                 f'5. 固定第31日特征分组未经样本外验证，不能据此确认三项指标的预测优势；放量突破规则的完整90日事件为{len(signals)}个，其中{wins}个盘中触及5倍。这里只观察路径，不是可实现收益或买入胜率。规则仍是待验证的研究触发器。',
                 f'6. AKE展示了持有期限的影响：固定放量规则首次触发后的下一日参考价，90日末收益为{fmt(ake_90*100 if ake_90 is not None else None)}%，更长观察期盘中峰值为{fmt(ake_signal.get("peak_multiple"))}倍。不能把日后高点直接归为90日策略成功。',
                 '### 上市时间字段审计',
                 '对七个指定项目另以startTime=0检查最早可得Alpha日线；结果保存在listing-date-check.json。最早可得K线也不等于官方首次上市公告。']
    if checks_bad:
        for c in checks_bad:
            conclusions.append(f'- {c["symbol"]}：最早可得Alpha日线是 {c["first_available_day"][:10]}，早于当前名录listingTime的 {c["listing_metadata"][:10]}。这证明当前字段不能直接当作首次上市日。本报告的主表只能叫“当前元数据基准”，相关事件研究需另核公告。')
    conclusions+=['### 转成监控方法',
                  '|层级|监控内容|目前实现与证据|','|---|---|---|',
                  '|身份/条件|链+地址、Alpha名录、USDT永续、报价一致性|已实现；多地址歧义隔离，合约身份仍需公告核实|',
                  '|估值空间|流通市值、FDV、100倍隐含市值|已实现当前快照；不推断过去市值|',
                  '|二次启动|连续30日后的放量突破，约束7日过热|已实现并做探索性事件比较，尚无预测优势证明|',
                  '|市场拥挤|资金费率、合约成交额、持仓量快照|已实现，未回测历史OI或资金费率规律|',
                  '|链上质量|池深度、持有人变化、独立买家、集中持仓/关联钱包|仅聚合流动性/持有人快照和部分DEX池数据；其余缺失|',
                  '|结果跟踪|每轮快照、本地阈值事件与历史回撤|已实现；真实买卖与退出收益未模拟|']
    idx=lines.index('## 指定项目')
    lines[idx:idx]=conclusions
    md='\n'.join(line if line.startswith('|') else '\n'+line+'\n' for line in lines).strip()+'\n'
    md=md.replace('缺失x','缺失')
    (base/'研究报告.md').write_text(md,encoding='utf-8')
    with (base/'research.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['symbol','price_market','alpha_id','address','alpha_listing','futures_listing','listing_order','peak_multiple','current_multiple','max_close_drawdown','coverage_delay_days','90d_complete','90d_peak_multiple']
        w=csv.DictWriter(f,fields);w.writeheader()
        for x in rows:w.writerow({k:(x.get(k) if k in x else x['after_both'].get(k)) for k in fields})

def snapshot_score(t,ticker,premium):
    cap=num(t.get('marketCap'));fdv=num(t.get('fdv'));liq=num(t.get('liquidity'));vol=num(t.get('volume24h'))
    change=num(t.get('percentChange24h'));fvol=num((ticker or {}).get('quoteVolume'));fund=num((premium or {}).get('lastFundingRate'))
    p=num(t.get('price'));fp=num((ticker or {}).get('lastPrice'));den=num(t.get('denomination')) or 1
    # Official alias may include units (e.g. 1000CHEEMS). denomination must explain pricing.
    price_ratio=ratio(fp,p*den if p else None)
    reasons=[];blocks=[];score=0
    if cap is None or cap<=0:blocks.append('缺流通市值')
    elif cap<=50e6:score+=20;reasons.append('流通市值≤5000万美元')
    elif cap<=100e6:score+=10
    if fdv and cap and cap>fdv*1.05:blocks.append('流通市值高于FDV，供应数据矛盾')
    if fdv and cap and fdv>=cap and cap/fdv>=.3:score+=10;reasons.append('报告流通比例≥30%')
    elif fdv and cap and cap/fdv<.1:blocks.append('流通比例不足10%，稀释风险')
    if liq is None or liq<100000:blocks.append('报告池流动性低于10万美元或缺失')
    else:score+=15
    if vol and vol>=100000:score+=10
    if fvol and fvol>=1e6:score+=10
    else:blocks.append('合约成交额不足或缺失')
    if fund is None:blocks.append('缺资金费率')
    elif abs(fund)>.001:blocks.append('本期资金费率绝对值>0.1%')
    else:score+=5
    if price_ratio is None or not .8<=price_ratio<=1.2:blocks.append('两市场单位价格偏差>20%或缺失，身份/报价待核实')
    if change is not None and change>100:blocks.append('24h已上涨超过100%，过热')
    if cap and cap*100>10e9:blocks.append('100倍静态流通市值将超过100亿美元')
    return {'screen_score':score,'score_basis':'unvalidated_watchlist_heuristic_max100','reasons':reasons,'blocks':blocks,
            'market_cap':cap,'fdv':fdv,'liquidity':liq,'volume24h':vol,'futures_quote_volume':fvol,'funding_rate':fund,
            'circulating_ratio':ratio(cap,fdv),'implied_100x_market_cap':cap*100 if cap else None,'price_ratio':price_ratio,
            'holders':num(t.get('holders')),'change24h':change,'price':p,'fresh_breakout':False,
            'unknowns':['去除池/桥/交易所的持仓集中度','关联钱包/对刷','解锁日历','LP控制权','合约安全与卖出模拟','历史市值/历史持仓']}

def select_enrichment(rows,limit):
    return sorted(rows,key=lambda x:(bool(x['blocks']),x['history_checked_at'],-x['screen_score']))[:limit]

def scan(args):
    base=Path(args.data);api=PublicAPI(base/'raw');tokens=api.data(TOKENS);exchange=api.data(FUTURES+'/fapi/v1/exchangeInfo')
    selected_chain=getattr(args,'chain','56')
    pairs,issues=universe(tokens,exchange,chain=selected_chain,active=True)
    tickers=api.data(FUTURES+'/fapi/v1/ticker/24hr');premiums=api.data(FUTURES+'/fapi/v1/premiumIndex')
    if not isinstance(tickers,list) or not tickers or not isinstance(premiums,list) or not premiums:raise ValueError('Expected ticker/premium arrays')
    ticks={x['symbol']:x for x in tickers};prems={x['symbol']:x for x in premiums}
    asof=utc();rows=[];errors=[]
    for pair in pairs:
        t,f=pair['token'],pair['future'];v=snapshot_score(t,ticks.get(f['symbol']),prems.get(f['symbol']))
        # Timestamp guards: receipt time cannot make old exchange prices fresh.
        now=int(time.time()*1000)
        for label,row,field in [('ticker',ticks.get(f['symbol'],{}),'closeTime'),('premium',prems.get(f['symbol'],{}),'time')]:
            ts=num(row.get(field))
            if ts is None or now-ts>900000 or ts>now+60000:v['blocks'].append(label+'时间戳缺失/过期')
        v.update(chain_id=str(t['chainId']),symbol=t['symbol'],alpha_id=t['alphaId'],address=t['contractAddress'],futures_symbol=f['symbol'],
                 identity=pair['identity'],as_of=asof,alpha_listing=utc(t['listingTime']),futures_listing=utc(f['onboardDate']),
                 history_features=None,open_interest=None,dex=None,ticker_time=num(ticks.get(f['symbol'],{}).get('closeTime')),funding_time=num(prems.get(f['symbol'],{}).get('time')))
        if v['price_ratio'] is not None and .8<=v['price_ratio']<=1.2:
            v['identity']='official_alias_or_unique_symbol_with_price_consistency_not_contract_level_futures_proof'
        rows.append(v)
    study_path=Path(__file__).resolve().parent/'data/research.json'
    study=json.loads(study_path.read_text()) if study_path.exists() else {'results':[]}
    historical={x['address'].lower():x for x in study.get('results',[])}
    for v in rows:
        v['research_peak_drawdown']=None;v['risk_notes']=[]
        prior_case=historical.get(v['address'].lower()) if v['chain_id']=='56' else None
        if prior_case:
            out=prior_case.get('after_both',{});peak=(out.get('entry_price') or 0)*(out.get('peak_multiple') or 0)
            price=num(ticks.get(v['futures_symbol'],{}).get('lastPrice')) if prior_case.get('price_market')=='futures_proxy' else v['price']
            rel=ratio(price,peak)
            v['research_peak_drawdown']=rel-1 if rel is not None else None
            v['research_as_of']=study.get('as_of')
            if rel is not None and rel<.2:v['risk_notes'].append('已较研究窗口峰值下跌80%以上；既可能再启动，也可能持续衰退，不能据低价加分')
    # Rotate the expensive queries: low-ranked assets must not starve indefinitely.
    previous_path=base/'latest.json'
    prior=json.loads(previous_path.read_text()) if previous_path.exists() else {'candidates':[]}
    old_by_id={(x.get('chain_id','56'),x['address']):x for x in prior.get('candidates',[])}
    last_day_end=(int(time.time()*1000)//DAY)*DAY-1
    for v in rows:
        prev=old_by_id.get((v['chain_id'],v['address']),{})
        v['history_checked_at']=prev.get('history_checked_at',0)
        if prev.get('history_candle_end',0)>=last_day_end and prev.get('history_candle_end',0)<time.time()*1000:
            v['history_features']=prev.get('history_features');v['history_candle_end']=prev['history_candle_end']
            if early_signal(v['history_features']):v['fresh_breakout']=True;v['screen_score']+=30
    shortlist=select_enrichment(rows,args.enrich)
    for v in shortlist:
        v['history_checked_at']=int(time.time()*1000)
        try:
            raw=api.data(ALPHA+'/klines',{'symbol':v['alpha_id']+'USDT','interval':'1d','limit':65})
            b=candles(raw);fx=features_at(b,len(b)-1) if b else None;v['history_features']=fx
            v['history_candle_end']=b[-1]['end'] if b else 0
            if v['fresh_breakout']:v['screen_score']-=30;v['fresh_breakout']=False
            if fx and b[-1]['end']<last_day_end:v['blocks'].append('日线已过期');fx=None
            if early_signal(fx):v['screen_score']+=30;v['fresh_breakout']=True;v['reasons'].append('已收盘日线出现放量突破，待独立验证')
            oi=api.data(FUTURES+'/fapi/v1/openInterest',{'symbol':v['futures_symbol']})
            v['open_interest']=num(oi.get('openInterest'));v['open_interest_as_of']=oi.get('time')
            dex_chain={'56':'bsc','1':'ethereum','8453':'base','CT_501':'solana','42161':'arbitrum','137':'polygon'}.get(v['chain_id'])
            ps=api.data('https://api.dexscreener.com/token-pairs/v1/'+dex_chain+'/'+v['address']) if dex_chain else []
            valid={p.get('pairAddress'):p for p in (ps or []) if p.get('chainId')==dex_chain and str(p.get('baseToken',{}).get('address','')).lower()==v['address'].lower()}
            main=max(valid.values(),key=lambda p:num((p.get('liquidity') or {}).get('usd')) or 0,default=None)
            if main:v['dex']={'observed_base_pools':len(valid),'main_pool':main.get('pairAddress'),'liquidity':num((main.get('liquidity') or {}).get('usd')),
                              'volume24h':num((main.get('volume') or {}).get('h24')),'txns24h':(main.get('txns') or {}).get('h24'),
                              'pool_created_at':main.get('pairCreatedAt'),'url':main.get('url')}
        except Exception as e:errors.append({'symbol':v['symbol'],'error':str(e)});v['blocks'].append('补充数据未完成')
    for v in rows:
        for key in ['ticker_time','funding_time']:
            if v.get(key) is None or time.time()*1000-v[key]>900000:
                v['blocks'].append(key+'在扫描完成时已缺失/过期')
        if v['history_features'] is None:v['unknowns'].append('历史放量突破尚未计算或数据不足')
        if getattr(args,'safety_checks',False) and not v['blocks'] and v['screen_score']>=70 and v['fresh_breakout']:
            from safety import inspect,unlock_override
            risk=inspect(v['address'].lower(),base/'safety');unlock=unlock_override(v['address'].lower(),base)
            v['safety']=risk;v['unlock']=unlock;v['blocks'].extend(risk.get('flags',[]))
            if unlock.get('large_unlock_soon'):v['blocks'].append('7日内已披露解锁≥流通量5%')
            v['risk_notes'].append('安全快照 '+risk['status']+'；去除池/桥/交易所后集中度、关联钱包与完整解锁仍未核实')
        v['watch_candidate']=not v['blocks'] and v['screen_score']>=70 and v['fresh_breakout']
    rows.sort(key=lambda x:(not x['watch_candidate'],-x['screen_score']))
    report={'as_of':asof,'completed_at':utc(),'status':'partial' if errors else 'ok','chain':'BSC' if selected_chain=='56' else selected_chain,'count':len(rows),'identity_quarantine':issues,'errors':errors,'candidates':rows,
            'notice':'观察分不代表100倍概率；链上集中度/安全和历史因果未经验证。Alpha名录价格没有逐币更新时间。'}
    base.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(base/'radar.sqlite') as db:
        db.execute('CREATE TABLE IF NOT EXISTS scans(as_of TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,as_of TEXT,asset TEXT,event TEXT,payload TEXT)')
        old=db.execute('SELECT payload FROM scans ORDER BY as_of DESC LIMIT 1').fetchone();previous=json.loads(old[0]) if old else None
        oldrows={(v.get('chain_id','56'),v['address']):v for v in previous['candidates']} if previous else {}
        for v in rows:
            oldv=oldrows.get((v['chain_id'],v['address']));event=None
            if previous and oldv is None:event='新发现交集（不等于刚上市）'
            if v['watch_candidate'] and (not oldv or not oldv.get('watch_candidate')):event='首次进入观察阈值'
            if event:
                db.execute('INSERT INTO events(as_of,asset,event,payload) VALUES (?,?,?,?)',(asof,v['chain_id']+':'+v['address'],event,json.dumps(v,ensure_ascii=False)))
                print(json.dumps({'local_alert':event,'symbol':v['symbol'],'chain_id':v['chain_id'],'score':v['screen_score'],'as_of':asof},ensure_ascii=False),flush=True)
        db.execute('INSERT OR REPLACE INTO scans VALUES (?,?)',(asof,json.dumps(report,ensure_ascii=False)))
    dump(base/'latest.json',report);dashboard(base,report)
    print(json.dumps({'as_of':asof,'candidates':len(rows),'watch_candidates':sum(v['watch_candidate'] for v in rows),'quarantine':len(issues),'errors':len(errors),'dashboard':str(base/'看板.html')},ensure_ascii=False),flush=True)
    return report

def dashboard(base,r):
    cells=[]
    for v in r['candidates']:
        content=[v['symbol']+' ('+v.get('chain_id','56')+')',v['screen_score'],'观察候选' if v['watch_candidate'] else '待核实',fmt(v['market_cap']),fmt(v['implied_100x_market_cap']),fmt(v['liquidity']),fmt(v['change24h']),'; '.join(v['blocks']+v.get('risk_notes',[])) or '无行情门槛阻断；链上安全未知',v['address']]
        cells.append('<tr>'+''.join('<td>'+html.escape(str(c))+'</td>' for c in content)+'</tr>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BSC Alpha 雷达</title><style>body{font:15px system-ui;background:#101827;color:#e5eaf4;margin:30px}h1{color:#f6c84b}p{max-width:1050px;line-height:1.7}input{padding:12px;width:320px;background:#24304a;color:white;border:1px solid #657189}table{border-collapse:collapse;width:100%;margin-top:20px}td,th{padding:12px;text-align:left;border-bottom:1px solid #354157}th{color:#f6c84b}td:last-child{font:12px monospace}main{overflow:auto}.meta{color:#adb9cf}</style><h1>BSC Alpha × 永续合约雷达</h1>'''
    page=page.replace('BSC Alpha × 永续合约雷达','Alpha × 永续合约雷达（'+html.escape(r['chain'])+'）')
    page+=f'<p class="meta">UTC {html.escape(r["as_of"])} · 候选 {r["count"]} · 歧义隔离 {len(r["identity_quarantine"])} · 数据错误 {len(r["errors"])}</p><p>{html.escape(r["notice"])}</p>'
    page+='<p>评分为待验证的研究优先级。100倍隐含市值假定供应不变；缺失持仓/解锁数据不视为安全。表格显示最近一次成功扫描，时间戳过旧时请重新扫描。</p><input id="q" placeholder="搜索币名、地址或风险…" aria-label="搜索"><main><table><thead><tr>'
    page+=''.join('<th>'+s+'</th>' for s in ['代币','观察分','状态','流通市值$','100倍隐含市值$','报告流动性$','24h%','阻断原因','链上合约地址'])+'</tr></thead><tbody>'+''.join(cells)+'</tbody></table></main>'
    page+=f'<p id="freshness" data-time="{html.escape(r["as_of"])}"></p>'
    page+='<script>const stamp=document.getElementById("freshness");if(Date.now()-Date.parse(stamp.dataset.time)>1200000)stamp.textContent="注意：此快照超过20分钟，请重新扫描；不代表当前行情。";document.getElementById("q").addEventListener("input",e=>{for(const r of document.querySelectorAll("tbody tr"))r.hidden=!r.textContent.toLowerCase().includes(e.target.value.toLowerCase())})</script></html>'
    (base/'看板.html').write_text(page,encoding='utf-8')

def main():
    p=argparse.ArgumentParser(description='BSC Alpha public-data research and monitoring; no orders')
    p.add_argument('--chain',default='all',help='scan/watch chainId: all (default), 56 for BSC; research always BSC');p.add_argument('command',choices=['scan','watch','research']);p.add_argument('--data',default='data');p.add_argument('--enrich',type=int,default=10);p.add_argument('--interval',type=int,default=300);p.add_argument('--limit',type=int,default=0,help='research only; 0=all')
    args=p.parse_args()
    if args.enrich<0 or args.interval<60 or args.limit<0:p.error('enrich/limit≥0, interval≥60')
    if args.command=='research':research(args);return
    while True:
        try:result=scan(args)
        except (RuntimeError,ValueError,subprocess.SubprocessError,OSError) as e:
            dump(Path(args.data)/'health.json',{'as_of':utc(),'status':'failed','error':str(e)})
            print('scan failed:',e,flush=True)
            if args.command=='scan':raise SystemExit(1)
        else:dump(Path(args.data)/'health.json',{'as_of':utc(),'status':result['status'],'errors':len(result['errors'])})
        if args.command!='watch':break
        time.sleep(args.interval)

if __name__=='__main__':main()
