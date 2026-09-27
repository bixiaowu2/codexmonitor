from __future__ import annotations
import json, time
from datetime import datetime, timezone
from config import Config
from sources import Collector, load_universe
from scoring import score
from storage import Store
from forward import observe as observe_forward, register as register_forward, summary as forward_summary

VERSION = 'stock-v0.5.1'

def market_group(row):
    explicit = row.get('market_group')
    if explicit in ('中国股票', '美国股票', '其他市场'):
        return explicit
    # Listing market, not issuer domicile. Unknown instruments stay unclassified.
    symbol = str(row.get('symbol', ''))
    if symbol.endswith(('.SS', '.SZ', '.HK', '.BJ')):
        return '中国股票'
    if row.get('region') in ('US', '美国'):
        return '美国股票'
    return '其他市场'

def grouped_rows(rows):
    groups = {'中国股票': [], '美国股票': [], '其他市场': []}
    for row in rows:
        groups[market_group(row)].append(row)
    for group in groups.values():
        group.sort(key=lambda r: (-r.get('score_meta', {}).get('score', 0), r['symbol']))
    return groups

def market_label(row):
    subgroup = row.get('market_subgroup')
    return f'{market_group(row)}·{subgroup}' if subgroup else market_group(row)

def fmt(row, s):
    change = f'{row["change"]:.1f}%' if row.get('change') is not None else '未知'
    ratio = f'{row["volume_ratio"]:.1f}' if row.get('volume_ratio') is not None else '未知'
    quote_time = datetime.fromtimestamp(row['quote_at'], timezone.utc).strftime('%m-%d %H:%M UTC')
    return (f'📊 {market_label(row)} · {row["name"]} ({row["symbol"]})\n主题：{row["theme"]} · {row["region"]}\n'
            f'关注分 {s["score"]}/100；最新价 {row.get("price")} {row.get("currency") or ""}；较上交易日末根有效K线 {change}\n'
            f'5分钟成交量基准倍数 {ratio}；{"、".join(s["reasons"])}\n风险：{"；".join(s["risk"])}\n'
            f'行情截至 {quote_time}；公开行情可能延迟。\n这是研究提醒，主题事件尚未自动验证。')

def cycle(cfg, store, collector=None, universe=None):
    universe = load_universe(cfg.universe) if universe is None else universe
    rows, statuses = (collector or Collector(cfg.timeout)).collect(universe)
    for row in rows: row['score_meta'] = score(row)
    rows.sort(key=lambda r: (-r['score_meta']['score'], r['symbol']))
    rows = [r for group in grouped_rows(rows).values() for r in group[:cfg.max_candidates]]
    now = time.time(); previous = store.state('scores', {}) or {}; events = []
    channels = [x for x, on in [('telegram', cfg.telegram_enabled), ('dingtalk', cfg.dingtalk_enabled)] if on]
    current = {}
    for row in rows:
        s = row['score_meta']; key = row['symbol']; old = previous.get(key); current[key] = {'score': s['score']}
        kind = 'new_hot' if s['score'] >= 70 and not old else 'surge' if old and s['score'] - old.get('score', 0) >= 15 else None
        if kind and row.get('fresh', False):
            text = ('🚨 股票即时信号' if kind == 'new_hot' else '📈 股票评分跃升') + '\n' + fmt(row, s)
            track_key=f'{VERSION}:event:{kind}:{key}:{int(now//1800)}'
            for ch in channels: store.enqueue(f'{track_key}:{ch}', {'channel': ch, 'text': text}, now)
            events.append({'kind': kind, 'symbol': key, 'score': s['score'], 'track_key': track_key, 'entry': row.get('price'), 'instrument_key': key, 'market': market_label(row), 'payload': {'score': s, 'theme': row.get('theme'), 'kind': kind, 'quote_at': row.get('quote_at'), 'risk': s.get('risk', [])}})
    store.put('scores', current)
    with store.db() as d:
        for row in rows:
            if row.get('fresh') and isinstance(row.get('price'), (int,float)) and row['price']>0:
                observe_forward(d,row['symbol'],row['price'],row['quote_at'])
        for event in events:
            register_forward(d,event['track_key'],event['instrument_key'],event['symbol'],event['market'],event['entry'],event['payload'],now)
    for row in rows: store.put('row:' + row['symbol'], row)
    fresh_rows = [r for r in rows if r.get('fresh', False)]
    # Separate queue keys prevent one market suppressing another market later in the hour.
    bucket = int(now // cfg.ranking_interval)
    for label, group in grouped_rows(fresh_rows).items():
        if not channels or not group:
            continue
        text = f'股票雷达 · {label}关注排序（按上市交易市场）\n'
        text += time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now)) + '\n研究样本，不代表买入\n\n'
        text += '\n\n'.join(f'{i+1}. {fmt(r, r["score_meta"])}' for i, r in enumerate(group[:5]))
        for ch in channels:
            store.enqueue(f'{VERSION}:ranking:{label}:{bucket}:{ch}', {'channel': ch, 'market': label, 'text': text}, now)
    with store.db() as d: forward=forward_summary(d)
    report = {'version': VERSION, 'as_of': now, 'forward': forward, 'markets': grouped_rows(rows), 'rows': rows, 'events': events, 'sources': statuses,
              'errors': [s for s in statuses if s['status'] != 'ok'], 'notice': '主题标签用于研究分类，不等于公司获得订单、政策支持或股价必涨。'}
    store.put('latest', report); store.cleanup(now); return report
