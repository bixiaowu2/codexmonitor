from __future__ import annotations
import json,time
from config import Config
from inputs import enrich,wallet_index,kol_events,x_mentions,shared_x_mentions
from scoring import score
from sources import Collector
from storage import Store
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
    completed=time.time()
    with store.db() as d:
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
    report={'version':VERSION,'as_of':completed,'started_at':now,'pairs_seen':len(pairs),'candidates':len(rows),'errors':errors,'sources':statuses,'chains':chains,'rows':rows,'social':social_health,
            'notice':'公开DEX样本候选，不保证为Meme。X帖子来自共享采集或可选证据文件；提及不代表支持，不增加买入评分。钱包尚未自动跟踪。合约安全、集中度、解锁未知。'}
    store.put('latest',report);store.cleanup(completed);return report

def main():
    cfg=Config.load();print(json.dumps(cycle(cfg,Store(cfg.data)),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
