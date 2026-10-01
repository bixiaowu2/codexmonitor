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
