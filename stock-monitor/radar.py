from __future__ import annotations
import json, time
from config import Config
from sources import Collector, load_universe
from scoring import score
from storage import Store

VERSION = 'stock-v0.1'

def fmt(row, s):
    return (f'📊 股票候选 {row["name"]} ({row["symbol"]})\n主题：{row["theme"]} · {row["region"]}\n'
            f'关注分 {s["score"]}/100；最新价 {row.get("price")} {row.get("currency") or ""}；近期涨幅 {row.get("change", 0):.1f}%\n'
            f'成交量基准倍数 {row.get("volume_ratio", 0):.1f}；{"、".join(s["reasons"])}\n风险：{"；".join(s["risk"])}\n'
            '这是研究提醒，不构成投资建议。')

def cycle(cfg, store, collector=None, universe=None):
    universe = load_universe(cfg.universe) if universe is None else universe
    rows, statuses = (collector or Collector(cfg.timeout)).collect(universe)
    for row in rows: row['score_meta'] = score(row)
    rows.sort(key=lambda r: (-r['score_meta']['score'], r['symbol']))
    rows = rows[:cfg.max_candidates]
    now = time.time(); previous = store.state('scores', {}) or {}; events = []
    channels = [x for x, on in [('telegram', cfg.telegram_enabled), ('dingtalk', cfg.dingtalk_enabled)] if on]
    current = {}
    for row in rows:
        s = row['score_meta']; key = row['symbol']; old = previous.get(key); current[key] = {'score': s['score']}
        kind = 'new_hot' if s['score'] >= 70 and not old else 'surge' if old and s['score'] - old.get('score', 0) >= 15 else None
        if kind and channels:
            text = ('🚨 股票即时信号' if kind == 'new_hot' else '📈 股票评分跃升') + '\n' + fmt(row, s)
            for ch in channels: store.enqueue(f'{VERSION}:event:{kind}:{key}:{int(now//1800)}:{ch}', {'channel': ch, 'text': text}, now)
            events.append({'kind': kind, 'symbol': key, 'score': s['score']})
    store.put('scores', current)
    for row in rows: store.put('row:' + row['symbol'], row)
    if channels and rows and int(now // cfg.ranking_interval) != store.state('last_ranking_bucket', -1):
        text = '股票雷达关注排序（研究样本，不代表买入）\n' + time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now)) + '\n\n' + '\n\n'.join(f'{i+1}. {fmt(r, r["score_meta"])}' for i, r in enumerate(rows[:10]))
        for ch in channels: store.enqueue(f'{VERSION}:ranking:{int(now//cfg.ranking_interval)}:{ch}', {'channel': ch, 'text': text}, now)
        store.put('last_ranking_bucket', int(now // cfg.ranking_interval))
    report = {'version': VERSION, 'as_of': now, 'rows': rows, 'events': events, 'sources': statuses,
              'errors': [s for s in statuses if s['status'] != 'ok'], 'notice': '主题标签用于研究分类，不等于公司获得订单、政策支持或股价必涨。'}
    store.put('latest', report); store.cleanup(now); return report
