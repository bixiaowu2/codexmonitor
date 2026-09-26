from __future__ import annotations
import json,time
from config import Config
from inputs import enrich,wallet_index,kol_events,x_mentions,shared_x_mentions
from scoring import score
from sources import Collector
from storage import Store
from forward import observe as observe_forward, register as register_forward, summary as forward_summary
VERSION='meme-v0.3'

def fmt(p,s):
    risk='；'.join(s.get('risk') or [])
    mentions=[e for e in p.get('mention_sources',[]) if e.get('account') and e.get('url')]
    social=f'X提及：{p.get("x_account_count",0)} 个账号（提及不等于利好）\n' if p.get('x_account_count') else ''
    if mentions:social+=f'@{mentions[0]["account"]}：{mentions[0].get("excerpt","")[:60]}\n{mentions[0]["url"]}\n'
    return (f'🌋 Meme候选 {p["symbol"]} · {p["chain"]} · 关注分 {s["score"]}/100\n'
            f'价格 {p.get("price_usd")} USD；池流动性 {p.get("liquidity_usd")} USD\n'
            f'1h成交额 {p.get("volume_1h")} USD；'+ '、'.join(s.get('reasons') or [])+'\n'
            f'{social}风险：{risk}\n合约 {p["address"]}\n{p.get("url","")}\n'
            '这是研究提醒，不自动交易，不代表100倍概率。')

def event_text(p, s, kind, previous=None):
    title = {'new_hot': '🚨 Meme即时信号', 'surge': '📈 Meme评分跃升', 'risk': '⚠️ Meme风险变化'}.get(kind, '🔔 Meme事件')
    detail = {
        'new_hot': '首次进入高分区',
        'surge': f'评分由{previous.get("score", 0):g}升至{s["score"]:g}' if previous else '评分明显上升',
        'risk': '评分或流动性明显恶化',
    }[kind]
    risk = '；'.join(s.get('risk') or [])
    return (f'{title} · {p["symbol"]} · {p["chain"]}\n'
            f'{detail}，当前关注分 {s["score"]}/100\n'
            f'价格 {p.get("price_usd")} USD；池流动性 {p.get("liquidity_usd")} USD；1h成交额 {p.get("volume_1h")} USD\n'
            f'{"、".join(s.get("reasons") or [])}\n风险：{risk}\n'
            f'合约 {p["address"]}\n{p.get("url", "")}\n'
            '这是条件式研究提醒，不自动交易，也不代表100倍概率。')

def enqueue_events(cfg, store, rows, previous, now):
    channels = [name for name, on in [('telegram', cfg.telegram_enabled), ('dingtalk', cfg.dingtalk_enabled)] if on]
    events = []
    for p in rows:
        s = p['score_meta']; key = p['chain'] + ':' + p['address']; old = previous.get(key)
        kind = None
        if s['score'] >= 70 and not old:
            kind = 'new_hot'
        elif old and s['score'] - old.get('score', 0) >= 15:
            kind = 'surge'
        elif old and (old.get('liquidity_usd') or 0) > 0 and (p.get('liquidity_usd') or 0) <= old['liquidity_usd'] * 0.6:
            kind = 'risk'
        if not kind:
            continue
        body = event_text(p, s, kind, old)
        track_key=f'{VERSION}:event:{kind}:{key}:{int(now//1800)}'
        for channel in channels:
            store.enqueue(f'{track_key}:{channel}', {'channel': channel, 'text': body}, now)
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
    rows=[]
    for p in representatives.values():
        # External labels remain contextual evidence, not unverified score bonuses.
        s=score(p);p['score_meta']=s
        if s['score']>0:rows.append(p)
    rows.sort(key=lambda p:(-p['score_meta']['score'],p['chain'],p['address']))
    rows=rows[:cfg.max_candidates]
    previous = store.state('candidate_scores', {}) or {}
    events = enqueue_events(cfg, store, rows, previous, now)
    current = {p['chain'] + ':' + p['address']: {'score': p['score_meta']['score'], 'liquidity_usd': p.get('liquidity_usd') or 0} for p in rows}
    store.put('candidate_scores', current)
    completed=time.time()
    with store.db() as d:
        for p in rows:
            price=p.get('price_usd')
            if isinstance(price,(int,float)) and price>0:
                observe_forward(d,p['chain']+':'+p['address'],price,now)
        for event in events:
            register_forward(d,event['track_key'],event['instrument_key'],event['chain']+':'+event['address'],event['market'],event['entry'],event['payload'],now)
        for p in pairs:d.execute('INSERT OR REPLACE INTO pairs VALUES(?,?,?)',(p['chain']+':'+p['address']+':'+p['pair_address'],completed,json.dumps(p,ensure_ascii=False)))
        for p in rows:
            key=f'{VERSION}:{p["chain"]}:{p["address"]}:{int(now//900)}'
            d.execute('INSERT OR IGNORE INTO signals VALUES(?,?,?)',(key,completed,json.dumps(p,ensure_ascii=False)))
    channels=[name for name,on in [('telegram',cfg.telegram_enabled),('dingtalk',cfg.dingtalk_enabled)] if on] if rows else []
    errors=[s['chain']+':'+s['endpoint']+':'+s.get('error',s['status']) for s in statuses if s['status'] not in ('ok','empty')]
    if social_health['status'] not in ('ok','disabled'):errors.append('x-monitor:'+social_health['status'])
    if channels:
        body='Meme雷达 每小时关注排序（样本排名，不代表全链覆盖）\n'+time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime(completed))+'\n'
        body+='数据状态：'+('部分接口异常' if errors else '本轮接口正常')+'\n\n'
        body+='\n\n'.join(f'{i+1}. '+fmt(p,p['score_meta']) for i,p in enumerate(rows[:5]))
        for channel in channels:store.enqueue(f'{VERSION}:ranking:{int(now//3600)}:{channel}',{'channel':channel,'text':body},completed)
    chains={chain:{'pairs':sum(p['chain']==chain for p in pairs),'candidates':sum(p['chain']==chain for p in rows),'status':'ok' if all(s['status'] in ('ok','empty') for s in statuses if s['chain']==chain) else 'partial'} for chain in cfg.chains}
    with store.db() as d: forward=forward_summary(d)
    report={'version':VERSION,'as_of':completed,'started_at':now,'pairs_seen':len(pairs),'candidates':len(rows),'errors':errors,'sources':statuses,'chains':chains,'rows':rows,'events':events,'forward':forward,'social':social_health,
            'notice':'公开DEX样本候选，不保证为Meme。X帖子来自共享采集或可选证据文件；提及不代表支持，不增加买入评分。钱包尚未自动跟踪。合约安全、集中度、解锁未知。'}
    store.put('latest',report);store.cleanup(completed);return report

def main():
    cfg=Config.load();print(json.dumps(cycle(cfg,Store(cfg.data)),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
