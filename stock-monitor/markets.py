"""Group by listing exchange; each market has independent candidate and queue slots."""
def market_group(row):
    symbol=str(row.get('symbol','')).upper()
    if symbol.endswith(('.SS','.SZ','.BJ')):return 'A股'
    if symbol.endswith('.HK'):return '港股'
    if row.get('market_group') in ('美股','美国股票') or row.get('region') in ('US','美国'):return '美股'
    return '其他市场'

def grouped_rows(rows):
    groups={name:[] for name in ('A股','港股','美股','其他市场')}
    for row in rows:groups[market_group(row)].append(row)
    for group in groups.values():group.sort(key=lambda r:(-r.get('score_meta',{}).get('score',0),r['symbol']))
    return groups

def market_label(row):
    return market_group(row)

# 推送里按这个顺序分段(用户习惯: A股 → 港股 → 美股)
MARKET_ORDER = ('A股', '港股', '美股', '韩股', '其他市场')


def market_of(symbol):
    """按 symbol 后缀判市场。与 stock_scan 的 mkt_of 口径一致, 只是提到这里共用。"""
    sym = str(symbol or '').upper()
    if sym.endswith(('.SS', '.SZ', '.BJ')):
        return 'A股'
    if sym.endswith('.HK'):
        return '港股'
    if '.KS' in sym:
        return '韩股'
    return '美股'
