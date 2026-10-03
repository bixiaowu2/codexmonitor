from __future__ import annotations
import json,time
from config import Config
from inputs import enrich,wallet_index,kol_events,x_mentions,shared_x_mentions
from scoring import score
from sources import Collector,_num
from storage import Store
from forward import observe as observe_forward, register as register_forward, summary as forward_summary
from safety import Checker
from wallet_rules import evidence as wallet_evidence, render as wallet_text
from post_rules import evaluate as post_evidence
from gmgn import Client as GmgnClient
from binance_web3 import read as read_binance_web3, apply as apply_binance_web3, live_enrich, audit_flags
VERSION='meme-v0.5.3'

SAFETY_FIELDS={'buy_tax':'买入税','sell_tax':'卖出税','cannot_sell_all':'完整卖出限制',
               'holders':'持仓集中度','is_honeypot':'蜜罐检测','cannot_buy':'买入限制',
               'transfer_pausable':'暂停转账权限','is_blacklisted':'黑名单权限',
               'owner_change_balance':'修改余额权限','is_proxy':'代理升级权限',
               'is_mintable':'增发权限','selfdestruct':'自毁权限',
               'slippage_modifiable':'修改税费权限','personal_slippage_modifiable':'针对地址改税',
               'can_take_back_ownership':'收回所有权','hidden_owner':'隐藏所有者'}

def safety_text(p):
    labels = {'safe': '未检出已知硬风险（不保证可卖）', 'blocked': '已阻断',
              'unknown': '未知，仅观察', 'disabled': '未启用，仅观察'}
    line = '安全：' + labels.get(p.get('safety_status'), '未知，仅观察')
    if p.get('safety_checked_at'):
        line += '；GoPlus ' + time.strftime('%m-%d %H:%M UTC', time.gmtime(p['safety_checked_at']))
    if p.get('safety_blocks'):
        line += '；阻断：' + '、'.join(p['safety_blocks'])
    warnings=[w for w in p.get('safety_warnings',[]) if not w.startswith('关键安全字段缺失：')
              and not (w=='持仓集中度数据缺失或无效' and p.get('safety_coverage_missing'))]
    if warnings:line += '；警告：' + '、'.join(warnings)
    missing=p.get('safety_coverage_missing') or []
    if missing:
        names=[SAFETY_FIELDS.get(k,k) for k in missing[:5]]
        line+='\n数据缺口：供应商未返回有效的'+ '、'.join(names)
        if len(missing)>5:line+=f'等 {len(missing)} 项'
        line+='；缺失不等于安全，也不等于已确认不能卖出。'
    elif any(w.startswith('关键安全字段缺失：') for w in p.get('safety_warnings',[])):
        line+='；关键安全字段不完整，仅观察'
    return line


def event_reasons(p, previous):
    if not previous:return []
    reasons=[]
    if p.get('safety_status')=='blocked' and previous.get('safety_status')!='blocked':
        reasons.append('新增已报告的安全风险：'+'、'.join(p.get('safety_blocks') or ['安全检查阻断']))
    elif previous.get('safety_eligible') and not p.get('safety_eligible') and p.get('safety_status')=='safe':
        warnings=[w for w in p.get('safety_warnings',[]) if w!='持仓集中度数据缺失或无效' and not w.startswith('关键安全字段缺失：')]
        if warnings:reasons.append('新增安全警告：'+'、'.join(warnings))
    old_liq,new_liq=_num(previous.get('liquidity_usd')),_num(p.get('liquidity_usd'))
    # Missing reserves and different representative pools are not evidence of a drain.
    comparable=(p.get('pair_address') and p.get('pair_address')==previous.get('pair_address')
                and p.get('source')==previous.get('source'))
    if comparable and old_liq is not None and old_liq>0 and new_liq is not None and 0<=new_liq<=old_liq*.6:
        reasons.append(f'同池报告流动性 {old_liq:,.2f} → {new_liq:,.2f} USD，下降 {1-new_liq/old_liq:.1%}（美元价值变化，不直接证明撤池）')
    return reasons


def safety_coverage(rows):
    out={}
    for p in rows:
        chain=out.setdefault(p['chain'],{'checked_candidates':0,'eligible':0,'partial_fields':0,'unavailable':0,'missing_fields':{}})
        chain['checked_candidates']+=1;chain['eligible']+=int(bool(p.get('safety_eligible')))
        state=p.get('safety_coverage_status','unavailable')
        if state in ('partial_fields','unavailable'):chain[state]+=1
        for field in p.get('safety_coverage_missing',[]):chain['missing_fields'][field]=chain['missing_fields'].get(field,0)+1
    return out


def data_quality(statuses, social_health):
    """Turn raw provider states into a compact, actionable quality summary."""
    counts={}
    for s in statuses:
        state=s.get('status','unknown')
        if state not in ('ok','empty'):
            reason=s.get('error') or state
            counts[reason]=counts.get(reason,0)+1
    if social_health.get('status') not in ('ok','disabled'):
        counts['x-monitor:'+str(social_health.get('status'))]=1
    chains={}
    for chain in sorted({s.get('chain') for s in statuses if s.get('chain')}):
        ss=[s for s in statuses if s.get('chain')==chain]
        good=sum(s.get('status') in ('ok','empty') for s in ss)
        chains[chain]={'ok_endpoints':good,'endpoints':len(ss),'status':'ok' if good==len(ss) else 'partial'}
    return {'issue_counts':counts,'chains':chains,'social':social_health}

def fmt(p,s):
    risk='；'.join(r for r in s.get('risk',[]) if r not in p.get('safety_warnings',[])) or '请见上方安全状态'
    safety_line=safety_text(p)
    web3=p.get('binance_web3')
    web3_line=''
    if web3:
        checked=time.strftime('%m-%d %H:%M UTC',time.gmtime(web3.get('checked_at',0)))
        kinds='、'.join(str(k) for k in ('audit','info','rank','signal','rush') if k in web3)
        web3_line=f'Binance Web3 Skills只读证据：{web3.get("provider")}；检查 {checked}；类型 {kinds or "未标注"}\n'
    mentions=[e for e in p.get('mention_sources',[]) if e.get('account') and e.get('url')]
    social=f'X提及：{p.get("x_account_count",0)} 个账号（提及不等于利好）\n' if p.get('x_account_count') else ''
    if mentions:social+=f'@{mentions[0]["account"]}：{mentions[0].get("excerpt","")[:60]}\n{mentions[0]["url"]}\n'
    timing=''
    if p.get('first_seen_at') and p.get('fetched_at'):
        timing='本观察期首次记录 '+time.strftime('%m-%d %H:%M UTC',time.gmtime(p['first_seen_at']))+'；行情采集 '+time.strftime('%m-%d %H:%M UTC',time.gmtime(p['fetched_at']))+'（非最后成交时间）\n'
    research='低市值早期观察筛选通过；未验证盈利优势，不加买入分。\n' if p.get('post_research',{}).get('matched') else ''
    return (f'🌋 Meme候选 {p["symbol"]} · {p["chain"]} · 关注分 {s["score"]}/100\n'
            f'价格 {p.get("price_usd")} USD；池流动性 {p.get("liquidity_usd")} USD\n'
            f'1h成交额 {p.get("volume_1h")} USD；'+ '、'.join(s.get('reasons') or [])+'\n'
            f'{safety_line}\n'
            f'{web3_line}'
            f'{timing}{research}'
            f'{wallet_text(p["wallet_research"]) + chr(10) if p.get("wallet_research",{}).get("status")=="provided_evidence" else ""}'
            f'{social}风险：{risk}\n合约 {p["address"]}\n{p.get("url","")}\n'
            '这是研究提醒，不自动交易，不代表100倍概率。')

def event_text(p, s, kind, previous=None):
    title = {'new_hot': '🚨 Meme即时信号', 'surge': '📈 Meme评分跃升', 'risk': '⚠️ Meme风险变化', 'data_gap':'ℹ️ Meme安全数据缺口'}.get(kind, '🔔 Meme事件')
    detail = {
        'new_hot': '首次进入高分区',
        'surge': f'评分由{previous.get("score", 0):g}升至{s["score"]:g}' if previous else '评分明显上升',
        'risk': '；'.join(event_reasons(p,previous)) or '风险条件变化，请复核',
        'data_gap': '此前安全检查通过，本轮安全数据不完整，暂停积极信号；不代表已确认安全恶化',
    }[kind]
    risk = '；'.join(r for r in s.get('risk',[]) if r not in p.get('safety_warnings',[])) or '请见下方安全状态'
    safety_line=safety_text(p)
    web3=p.get('binance_web3')
    web3_line=''
    if web3:
        kinds='、'.join(str(k) for k in ('audit','info','rank','signal','rush') if k in web3)
        web3_line=f'Binance Web3 Skills只读证据：{web3.get("provider")}；类型 {kinds or "未标注"}\n'
    return (f'{title} · {p["symbol"]} · {p["chain"]}\n'
            f'{detail}，当前关注分 {s["score"]}/100\n'
            f'价格 {p.get("price_usd")} USD；池流动性 {p.get("liquidity_usd")} USD；1h成交额 {p.get("volume_1h")} USD\n'
            f'{"、".join(s.get("reasons") or [])}\n风险：{risk}\n'
            f'{safety_line}\n'
            f'{web3_line}'
            f'合约 {p["address"]}\n{p.get("url", "")}\n'
            '这是条件式研究提醒，不自动交易，也不代表100倍概率。')

def enqueue_events(cfg, store, rows, previous, now):
    channels = [name for name, on in [('telegram', cfg.telegram_enabled), ('dingtalk', cfg.dingtalk_enabled)] if on]
    events = []
    for p in rows:
        s = p['score_meta']; key = p['chain'] + ':' + p['address']; old = previous.get(key)
        kind = None
        eligible=p.get('safety_eligible',False)
        safety_status=p.get('safety_status','unknown')
        # Risk changes take precedence over optimistic price/score movements.
        if event_reasons(p,old):
            kind='risk'
        elif old and old.get('safety_eligible') and not eligible:
            kind='data_gap'
        elif eligible and s['score']>=70 and (not old or old.get('score',0)<70 or not old.get('safety_eligible')):
            kind='new_hot'
        elif eligible and old and s['score']-old.get('score',0)>=15:
            kind='surge'
        if not kind:
            continue
        body = event_text(p, s, kind, old)
        track_key=f'{VERSION}:event:{kind}:{key}:{int(now//(21600 if kind=="data_gap" else 1800))}'
        for channel in channels:
            store.enqueue(f'{track_key}:{channel}', {'channel': channel, 'text': body, 'kind': kind, 'instrument_keys': [key]}, now)
        events.append({'kind': kind, 'chain': p['chain'], 'address': p['address'], 'score': s['score'], 'track_key': track_key, 'entry': p.get('price_usd'), 'instrument_key': key, 'market': p['chain'], 'payload': {'score': s, 'reasons': s.get('reasons', []), 'risk': s.get('risk', [])}})
    return events

def cycle(cfg,store,collector=None):
    now=time.time(); collector=collector or Collector(cfg.timeout)
    pairs,statuses=collector.collect(cfg.chains)
    binance_web3, binance_web3_health = read_binance_web3(cfg.binance_web3_file, pairs, now,
                                                           cfg.binance_web3_max_age)
    apply_binance_web3(pairs, binance_web3)
    if cfg.binance_web3_live and cfg.binance_web3_live_checks:
        live_health = live_enrich(pairs, cfg.binance_web3_live_checks, cfg.timeout)
        binance_web3_health = {**binance_web3_health, **live_health,
                               'file_matched': binance_web3_health.get('matched', 0),
                               'live_enabled': True}
    else:
        binance_web3_health['live_enabled'] = False
    # Each candidate needs one info and one security query; the setting is a
    # candidate budget, while the client request budget is doubled.
    gmgn=GmgnClient(cfg.gmgn_api_key, cfg.gmgn_timeout, cfg.gmgn_max_checks * 2)
    gmgn_health=gmgn.enrich(pairs, cfg.gmgn_max_checks) if cfg.gmgn_max_checks else {'status':'disabled','requests':0,'errors':0,'enriched':0}
    shared_events,social_health=shared_x_mentions(cfg.public_feed_db,pairs,time.time())
    wallets=wallet_index(cfg.wallet_file)
    pairs=enrich(pairs,wallets,kol_events(cfg.kol_file,now),x_mentions(cfg.x_feed_file,now)+shared_events)
    # Representative pool selected by reserve, not the pool with the most optimistic score.
    representatives={}
    for p in pairs:
        key=(p['chain'],p['address'])
        if key not in representatives or (p.get('liquidity_usd') or 0)>(representatives[key].get('liquidity_usd') or 0):representatives[key]=p
    previous = store.state('candidate_scores', {}) or {}
    seen = store.state('candidate_first_seen', {}) or {}
    seen = {k:v for k,v in seen.items() if isinstance(v,dict) and 0<=now-v.get('last_seen',0)<=7*86400}
    rows=[]
    for p in representatives.values():
        key=p['chain']+':'+p['address']
        seen[key]={'first_seen':seen.get(key,{}).get('first_seen',now),'last_seen':now}
        p['first_seen_at']=seen[key]['first_seen']
        # External labels remain contextual evidence, not unverified score bonuses.
        # Discard any stale/mocked upstream safety fields before base ranking.
        for field in list(p):
            if field.startswith('safety_'):p.pop(field)
        s=score(p);p['score_meta']=s
        if s['score']>0 or p['chain']+':'+p['address'] in previous:rows.append(p)
    # Safety is applied only to viable base candidates, with a bounded request
    # budget. Unchecked candidates remain visible as unknown and cannot trigger
    # an immediate buy-style event.
    safety_cache=store.state('safety_cache',{}) or {}
    checker=Checker(cfg.safety_enabled, cfg.timeout, safety_cache, cfg.safety_cache_seconds,
                    cfg.safety_max_checks, min(30, max(0, 220-(time.time()-now))))
    rows.sort(key=lambda p:(-p['score_meta']['score'],p['chain'],p['address']))
    for p in rows:
        result=checker.check(p)
        p['safety_status']=result.get('status','unknown')
        p['safety_blocks']=result.get('blocks') or []
        p['safety_warnings']=result.get('warnings') or []
        p['safety_provider']=result.get('provider','')
        p['safety_checked_at']=result.get('checked_at')
        p['safety_eligible']=result.get('eligible',False)
        p['safety_coverage_status']=result.get('coverage_status','unavailable')
        p['safety_coverage_missing']=result.get('coverage_missing') or result.get('missing') or []
        web3_blocks, web3_warnings = audit_flags(p)
        p['safety_blocks'] = list(dict.fromkeys(p['safety_blocks'] + web3_blocks))
        p['safety_warnings'] = list(dict.fromkeys(p['safety_warnings'] + web3_warnings))
        if web3_blocks:
            p['safety_status'] = 'blocked'; p['safety_eligible'] = False
        p['score_meta']=score(p)
        if wallets:
            p['wallet_research']=wallet_evidence(p,wallets,time.time())
    store.put('safety_cache',checker.snapshot())
    store.put('candidate_first_seen',seen)
    assessed=rows
    for p in representatives.values():
        p['post_research']=post_evidence(p,time.time())
    safety_counts={s:sum(p.get('safety_status')==s for p in assessed) for s in ('safe','blocked','unknown','disabled')}
    safety_counts.update(errors=checker.api_errors, requests=checker.requests, cache_hits=checker.cache_hits,
                         eligible=sum(p['safety_eligible'] for p in assessed))
    coverage=safety_coverage(assessed)
    rows=[p for p in assessed if p['score_meta']['score']>0 and p['safety_status']!='blocked']
    rows.sort(key=lambda p:(not p['safety_eligible'],-p['score_meta']['score'],p['chain'],p['address']))
    rows=rows[:cfg.max_candidates]
    # Cancel queued optimistic messages if a later check invalidates them.
    observed={p['chain']+':'+p['address']:p for p in assessed}
    with store.db() as d:
        for queued in d.execute("SELECT key,payload FROM outbox WHERE state='pending'").fetchall():
            payload=json.loads(queued['payload'])
            invalid=payload.get('kind') in ('new_hot','surge','ranking') and any(key in observed and (observed[key]['safety_status']=='blocked' or
                        (payload.get('kind') in ('new_hot','surge') and not observed[key]['safety_eligible']))
                        for key in payload.get('instrument_keys',[]))
            if invalid:d.execute("UPDATE outbox SET state='expired',error='safety_changed' WHERE key=?",(queued['key'],))
    event_rows=rows+[p for p in assessed if p not in rows and p['chain']+':'+p['address'] in previous]
    events = enqueue_events(cfg, store, event_rows, previous, now)
    current = {p['chain'] + ':' + p['address']: {'score': p['score_meta']['score'], 'liquidity_usd': p.get('liquidity_usd'),
               'pair_address':p.get('pair_address'),'source':p.get('source'),
               'safety_status':p['safety_status'],'safety_eligible':p['safety_eligible']} for p in assessed}
    store.put('candidate_scores', current)
    completed=time.time()
    with store.db() as d:
        for p in representatives.values():
            price=p.get('price_usd')
            if isinstance(price,(int,float)) and price>0:
                observe_forward(d,p['chain']+':'+p['address'],price,now)
        for event in events:
            if event['kind']=='data_gap':continue
            register_forward(d,event['track_key'],event['instrument_key'],event['chain']+':'+event['address'],event['market'],event['entry'],event['payload'],now)
        for p in assessed:
            if p.get('wallet_research',{}).get('observation_eligible') and isinstance(p.get('price_usd'),(int,float)) and p['price_usd']>0:
                instrument=p['chain']+':'+p['address']
                register_forward(d,'wallet-convergence-observe-v1:'+instrument+':'+str(int(now//86400)),
                                 instrument,instrument,p['chain'],p['price_usd'],
                                 {'kind':'wallet_observation','wallet_research':p['wallet_research'],'score_bonus':0},now)
        for p in representatives.values():
            if p['post_research']['observation_eligible']:
                instrument=p['chain']+':'+p['address']
                register_forward(d,'early-microcap-observe-v1:'+instrument+':'+str(int(now//86400)),
                                 instrument,instrument,p['chain'],p.get('price_usd'),
                                 {'kind':'early_microcap_observation','post_research':p['post_research'],'score_bonus':0},now)
        for p in pairs:d.execute('INSERT OR REPLACE INTO pairs VALUES(?,?,?)',(p['chain']+':'+p['address']+':'+p['pair_address'],completed,json.dumps(p,ensure_ascii=False)))
        for p in assessed:
            key=f'{VERSION}:{p["chain"]}:{p["address"]}:{int(now//900)}:{p["safety_status"]}'
            d.execute('INSERT OR IGNORE INTO signals VALUES(?,?,?)',(key,completed,json.dumps(p,ensure_ascii=False)))
    channels=[name for name,on in [('telegram',cfg.telegram_enabled),('dingtalk',cfg.dingtalk_enabled)] if on] if rows else []
    errors=[s['chain']+':'+s['endpoint']+':'+s.get('error',s['status']) for s in statuses if s['status'] not in ('ok','empty')]
    if safety_counts['unknown']:errors.append('safety:unknown:'+str(safety_counts['unknown']))
    if checker.api_errors:errors.append('safety:api_errors:'+str(checker.api_errors))
    if social_health['status'] not in ('ok','disabled'):errors.append('x-monitor:'+social_health['status'])
    if gmgn_health['status'] not in ('ok','disabled','empty'):errors.append('gmgn:'+str(gmgn_health.get('last_error') or gmgn_health['status']))
    if binance_web3_health['status'] not in ('ok','disabled','empty'):errors.append('binance_web3:'+binance_web3_health['status'])
    if channels:
        body='Meme雷达 每小时关注排序（样本排名，不代表全链覆盖）\n'+time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime(completed))+'\n'
        body+='数据状态：'+('数据或安全覆盖不完整' if errors else '本轮接口正常')+'\n'
        body+=f'GMGN只读：{gmgn_health["status"]}，增强 {gmgn_health.get("enriched",0)} 个候选；Binance Web3 Skills：{binance_web3_health["status"]}，匹配 {binance_web3_health.get("matched",0)} 个；均不改变安全阻断结论\n\n'
        body+=f'安全统计：safe {safety_counts["safe"]} / blocked {safety_counts["blocked"]} / unknown {safety_counts["unknown"]} / disabled {safety_counts["disabled"]}\n\n'
        body+='安全覆盖（本轮候选）：'+'；'.join(f'{chain} 字段不全 {v["partial_fields"]} / 无可用检查 {v["unavailable"]}' for chain,v in sorted(coverage.items()))+'\n'
        quality=data_quality(statuses,social_health)
        issue='；'.join(f'{k}×{v}' for k,v in sorted(quality['issue_counts'].items()))
        if issue: body+='数据缺失分类：'+issue+'\n'
        body+='链覆盖：'+'；'.join(f'{c} {v["status"]} {v["ok_endpoints"]}/{v["endpoints"]}' for c,v in quality['chains'].items())+'\n\n'
        ranked=[]
        for i,p in enumerate(rows[:5]):
            part=f'{i+1}. '+fmt(p,p['score_meta'])
            if len(body)+len(part)+80>3800:break
            body+=part+'\n\n';ranked.append(p)
        if len(ranked)<min(5,len(rows)):body+='消息长度限制，其余候选与完整安全记录保存在服务器。'
        for channel in channels:store.enqueue(f'{VERSION}:ranking:{int(now//3600)}:{channel}',{'channel':channel,'text':body,'kind':'ranking','instrument_keys':[p['chain']+':'+p['address'] for p in ranked]},completed)
    chains={chain:{'pairs':sum(p['chain']==chain for p in pairs),'candidates':sum(p['chain']==chain for p in rows),'status':'ok' if all(s['status'] in ('ok','empty') for s in statuses if s['chain']==chain) else 'partial'} for chain in cfg.chains}
    with store.db() as d: forward=forward_summary(d)
    report={'version':VERSION,'as_of':completed,'started_at':now,'pairs_seen':len(pairs),'candidates':len(rows),'errors':errors,'sources':statuses,'chains':chains,'rows':rows,'events':events,'forward':forward,'social':social_health,'gmgn':gmgn_health,'binance_web3':binance_web3_health,'data_quality':data_quality(statuses,social_health),'safety':safety_counts,'blocked_rows':[p for p in assessed if p['safety_status']=='blocked'][:20],
            'notice':'公开DEX样本候选，不保证为Meme。安全检查来自第三方字段，未自行模拟买卖，不能保证可卖；unknown/disabled/有警告只做观察，不触发积极即时信号。LP锁仓、关联钱包和解锁未验证。X帖子来自共享采集或可选证据文件；提及不代表支持，不增加买入评分。钱包尚未自动跟踪。'}
    report['safety_coverage']=coverage
    report['post_research']={'version':'early-microcap-observe-v1','sample_size':len(representatives),
                            'matched':sum(p['post_research']['matched'] for p in representatives.values()),
                            'unknown':sum(p['post_research']['status']=='unknown' for p in representatives.values()),
                            'safety_eligible_matches':sum(p['post_research']['observation_eligible'] for p in representatives.values()),
                            'score_bonus':0}
    report['wallet_research']={'status':'provided_evidence' if wallets else 'no_evidence_source',
                              'automatic_collection':False,'score_bonus':0,
                              'eligible':sum(p.get('wallet_research',{}).get('observation_eligible',False) for p in assessed)}
    store.put('latest',report);store.cleanup(completed);return report

def main():
    cfg=Config.load();print(json.dumps(cycle(cfg,Store(cfg.data)),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
