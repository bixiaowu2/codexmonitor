from __future__ import annotations
import json, time
from datetime import datetime, timezone
from config import Config
from sources import Collector, load_universe
from scoring import score
from storage import Store

VERSION = 'stock-v0.2'

def market_group(row):
    explicit = row.get('market_group')
    if explicit in ('中国股票', '美国股票', '其他市场'):
        return explicit
    region = str(row.get('region', ''))
    symbol = str(row.get('symbol', ''))
    if '中国' in region or symbol.endswith(('.SS', '.SZ', '.HK')):
        return '中国股票'
    if '美国' in region or ('.' not in symbol and region in ('US', '美国')):
        return '美国股票'
    return '其他市场'

def grouped_rows(rows):
    groups = {'中国股票': [], '美国股票': [], '其他市场': []}
    for row in rows:
        groups[market_group(row)].append(row)
    return groups

def fmt(row, s):
    change = f'{row["change"]:.1f}%' if row.get('change') is not None else '未知'
    ratio = f'{row["volume_ratio"]:.1f}' if row.get('volume_ratio') is not None else '未知'
    quote_time = datetime.fromtimestamp(row['quote_at'], timezone.utc).strftime('%m-%d %H:%M UTC')
    return (f'📊 {market_group(row)} · {row["name"]} ({row["symbol"]})\n主题：{row["theme"]} · {row["region"]}\n'
            f'关注分 {s["score"]}/100；最新价 {row.get("price")} {row.get("currency") or ""}；较上交易日末根有效K线 {change}\n'
            f'5分钟成交量基准倍数 {ratio}；{"、".join(s["reasons"])}\n风险：{"；".join(s["risk"])}\n'
            f'行情截至 {quote_time}；公开行情可能延迟。\n这是研究提醒，主题事件尚未自动验证。')

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
        if kind and channels and row.get('fresh', False):
            text = ('🚨 股票即时信号' if kind == 'new_hot' else '📈 股票评分跃升') + '\n' + fmt(row, s)
            for ch in channels: store.enqueue(f'{VERSION}:event:{kind}:{key}:{int(now//1800)}:{ch}', {'channel': ch, 'text': text}, now)
            events.append({'kind': kind, 'symbol': key, 'score': s['score']})
    store.put('scores', current)
    for row in rows: store.put('row:' + row['symbol'], row)
    fresh_rows = [r for r in rows if r.get('fresh', False)]
    if channels and fresh_rows and int(now // cfg.ranking_interval) != store.state('last_ranking_bucket', -1):
        sections = []
        for label, group in grouped_rows(fresh_rows).items():
            if group:
                sections.append(label + '\n' + '\n\n'.join(f'{i+1}. {fmt(r, r["score_meta"])}' for i, r in enumerate(group[:5])))
        text = '股票雷达关注排序（分市场；研究样本，不代表买入）\n' + time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now)) + '\n\n' + '\n\n'.join(sections)
        for ch in channels: store.enqueue(f'{VERSION}:ranking:{int(now//cfg.ranking_interval)}:{ch}', {'channel': ch, 'text': text}, now)
        store.put('last_ranking_bucket', int(now // cfg.ranking_interval))
    report = {'version': VERSION, 'as_of': now, 'rows': rows, 'events': events, 'sources': statuses,
              'errors': [s for s in statuses if s['status'] != 'ok'], 'notice': '主题标签用于研究分类，不等于公司获得订单、政策支持或股价必涨。'}
    store.put('latest', report); store.cleanup(now); return report
