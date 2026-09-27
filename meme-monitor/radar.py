from __future__ import annotations
import json,time
from config import Config
from inputs import enrich,wallet_index,kol_events,x_mentions,shared_x_mentions
from scoring import score
from sources import Collector
from storage import Store
from forward import observe as observe_forward, register as register_forward, summary as forward_summary
from safety import Checker
VERSION='meme-v0.5.1'

def safety_text(p):
    labels = {'safe': '未检出已知硬风险（不保证可卖）', 'blocked': '已阻断',
              'unknown': '未知，仅观察', 'disabled': '未启用，仅观察'}
    line = '安全：' + labels.get(p.get('safety_status'), '未知，仅观察')
    if p.get('safety_checked_at'):
        line += '；GoPlus ' + time.strftime('%m-%d %H:%M UTC', time.gmtime(p['safety_checked_at']))
    if p.get('safety_blocks'):
        line += '；阻断：' + '、'.join(p['safety_blocks'])
    if p.get('safety_warnings'):
        line += '；警告：' + '、'.join(p['safety_warnings'])
    return line


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
    risk='；'.join(s.get('risk') or [])
    safety_line=safety_text(p)
    mentions=[e for e in p.get('mention_sources',[]) if e.get('account') and e.get('url')]
    social=f'X提及：{p.get("x_account_count",0)} 个账号（提及不等于利好）\n' if p.get('x_account_count') else ''
    if mentions:social+=f'@{mentions[0]["account"]}：{mentions[0].get("excerpt","")[:60]}\n{mentions[0]["url"]}\n'
    return (f'🌋 Meme候选 {p["symbol"]} · {p["chain"]} · 关注分 {s["score"]}/100\n'
            f'价格 {p.get("price_usd")} USD；池流动性 {p.get("liquidity_usd")} USD\n'
            f'1h成交额 {p.get("volume_1h")} USD；'+ '、'.join(s.get('reasons') or [])+'\n'
            f'{safety_line}\n'
            f'{social}风险：{risk}\n合约 {p["address"]}\n{p.get("url","")}\n'
            '这是研究提醒，不自动交易，不代表100倍概率。')

def event_text(p, s, kind, previous=None):
    title = {'new_hot': '🚨 Meme即时信号', 'surge': '📈 Meme评分跃升', 'risk': '⚠️ Meme风险变化'}.get(kind, '🔔 Meme事件')
    detail = {
        'new_hot': '首次进入高分区',
        'surge': f'评分由{previous.get("score", 0):g}升至{s["score"]:g}' if previous else '评分明显上升',
        'risk': '安全状态或流动性恶化，请优先复核风险',
    }[kind]
    risk = '；'.join(s.get('risk') or [])
    safety_line=safety_text(p)
    return (f'{title} · {p["symbol"]} · {p["chain"]}\n'
            f'{detail}，当前关注分 {s["score"]}/100\n'
            f'价格 {p.get("price_usd")} USD；池流动性 {p.get("liquidity_usd")} USD；1h成交额 {p.get("volume_1h")} USD\n'
            f'{"、".join(s.get("reasons") or [])}\n风险：{risk}\n'
            f'{safety_line}\n'
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
        if old and ((safety_status=='blocked' and old.get('safety_status')!='blocked')
                    or (old.get('safety_eligible') and not eligible)
                    or ((old.get('liquidity_usd') or 0)>0 and (p.get('liquidity_usd') or 0)<=old['liquidity_usd']*.6)):
            kind='risk'
        elif eligible and s['score']>=70 and (not old or old.get('score',0)<70 or not old.get('safety_eligible')):
            kind='new_hot'
        elif eligible and old and s['score']-old.get('score',0)>=15:
            kind='surge'
        if not kind:
            continue
        body = event_text(p, s, kind, old)
        track_key=f'{VERSION}:event:{kind}:{key}:{int(now//1800)}'
        for channel in channels:
            store.enqueue(f'{track_key}:{channel}', {'channel': channel, 'text': body, 'kind': kind, 'instrument_keys': [key]}, now)
        events.append({'kind': kind, 'chain': p['chain'], 'address': p['address'], 'score': s['score'], 'track_key': track_key, 'entry': p.get('price_usd'), 'instrument_key': key, 'market': p['chain'], 'payload': {'score': s, 'reasons': s.get('reasons', []), 'risk': s.get('risk', [])}})
    return events

def cycle(cfg,store,collector=None):
    now=time.time(); collector=collector or Collector(cfg.timeout)
    pairs,statuses=collector.collect(cfg.chains)
    shared_events,social_health=shared_x_mentions(cfg.public_feed_db,pairs,time.time())
    pairs=enrich(pairs,wallet_index(cfg.wallet_file),kol_events(cfg.kol_file,now),x_mentions(cfg.x_feed_file,now)+shared_events)
    # Representative pool selected by reserve, not the pool with the most optimistic score.
    representatives={}
    for p in pairs:
        key=(p['chain'],p['address'])
        if key not in representatives or (p.get('liquidity_usd') or 0)>(representatives[key].get('liquidity_usd') or 0):representatives[key]=p
    previous = store.state('candidate_scores', {}) or {}
    rows=[]
    for p in representatives.values():
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
        p['score_meta']=score(p)
    store.put('safety_cache',checker.snapshot())
    assessed=rows
    safety_counts={s:sum(p.get('safety_status')==s for p in assessed) for s in ('safe','blocked','unknown','disabled')}
    safety_counts.update(errors=checker.api_errors, requests=checker.requests, cache_hits=checker.cache_hits,
                         eligible=sum(p['safety_eligible'] for p in assessed))
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
    current = {p['chain'] + ':' + p['address']: {'score': p['score_meta']['score'], 'liquidity_usd': p.get('liquidity_usd') or 0,
               'safety_status':p['safety_status'],'safety_eligible':p['safety_eligible']} for p in assessed}
    store.put('candidate_scores', current)
    completed=time.time()
    with store.db() as d:
        for p in representatives.values():
            price=p.get('price_usd')
            if isinstance(price,(int,float)) and price>0:
                observe_forward(d,p['chain']+':'+p['address'],price,now)
        for event in events:
            register_forward(d,event['track_key'],event['instrument_key'],event['chain']+':'+event['address'],event['market'],event['entry'],event['payload'],now)
        for p in pairs:d.execute('INSERT OR REPLACE INTO pairs VALUES(?,?,?)',(p['chain']+':'+p['address']+':'+p['pair_address'],completed,json.dumps(p,ensure_ascii=False)))
        for p in assessed:
            key=f'{VERSION}:{p["chain"]}:{p["address"]}:{int(now//900)}:{p["safety_status"]}'
            d.execute('INSERT OR IGNORE INTO signals VALUES(?,?,?)',(key,completed,json.dumps(p,ensure_ascii=False)))
    channels=[name for name,on in [('telegram',cfg.telegram_enabled),('dingtalk',cfg.dingtalk_enabled)] if on] if rows else []
    errors=[s['chain']+':'+s['endpoint']+':'+s.get('error',s['status']) for s in statuses if s['status'] not in ('ok','empty')]
    if safety_counts['unknown']:errors.append('safety:unknown:'+str(safety_counts['unknown']))
    if checker.api_errors:errors.append('safety:api_errors:'+str(checker.api_errors))
    if social_health['status'] not in ('ok','disabled'):errors.append('x-monitor:'+social_health['status'])
    if channels:
        body='Meme雷达 每小时关注排序（样本排名，不代表全链覆盖）\n'+time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime(completed))+'\n'
        body+='数据状态：'+('数据或安全覆盖不完整' if errors else '本轮接口正常')+'\n\n'
        body+=f'安全统计：safe {safety_counts["safe"]} / blocked {safety_counts["blocked"]} / unknown {safety_counts["unknown"]} / disabled {safety_counts["disabled"]}\n\n'
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
    report={'version':VERSION,'as_of':completed,'started_at':now,'pairs_seen':len(pairs),'candidates':len(rows),'errors':errors,'sources':statuses,'chains':chains,'rows':rows,'events':events,'forward':forward,'social':social_health,'data_quality':data_quality(statuses,social_health),'safety':safety_counts,'blocked_rows':[p for p in assessed if p['safety_status']=='blocked'][:20],
            'notice':'公开DEX样本候选，不保证为Meme。安全检查来自第三方字段，未自行模拟买卖，不能保证可卖；unknown/disabled/有警告只做观察，不触发积极即时信号。LP锁仓、关联钱包和解锁未验证。X帖子来自共享采集或可选证据文件；提及不代表支持，不增加买入评分。钱包尚未自动跟踪。'}
    store.put('latest',report);store.cleanup(completed);return report

def main():
    cfg=Config.load();print(json.dumps(cycle(cfg,Store(cfg.data)),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
