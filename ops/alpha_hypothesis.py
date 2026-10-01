"""Prospective, separate cohort for a user-supplied Alpha hypothesis; never changes scores."""
import concurrent.futures
import json
import time
from pathlib import Path
from holdings_core import number
from holdings_sources import public_json

VERSION = 'naive-alpha-observe-v1'
SOURCE = 'https://x.com/Naive_BNB/status/2105336679745921321'
HORIZONS = (3600, 21600, 86400, 604800)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS hypothesis_tracks(
        key TEXT PRIMARY KEY,address TEXT,symbol TEXT,opened REAL,entry REAL,last REAL,peak REAL,
        trough REAL,drawdown REAL,quote_time REAL,max_gap REAL,marks TEXT,evidence TEXT)''')


def assess(row, observation, spot, oi, premium, now):
    reasons = []; unknown = []
    q = observation.get('quote') or {}
    qt = number(q.get('time')); price = number(q.get('price'))
    if qt is None or not 0 <= now * 1000 - qt <= 180000 or price is None or price <= 0:
        unknown.append('缺新鲜Alpha成交报价')
    names = {str(row.get(k, '')).upper() for k in ('symbol', 'cexCoinName', 'spot_alias')} - {''}
    if spot is None:
        unknown.append('现货名录未核验')
    elif names & spot:
        reasons.append('币安现货名录已出现同名/官方别名，保守排除')
    if not observation.get('dual_matched'):
        reasons.append('未核验Alpha与交易中USDT永续交集')
    if observation.get('blocks'):
        reasons.append('原雷达门槛或风险阻断')
    safety = observation.get('safety') or {}
    if safety.get('status') != 'no_listed_risk_detected' or safety.get('flags'):
        unknown.append('安全检查未知或有警告')
    top = number(safety.get('raw_top10_share')); observed = number(safety.get('observed'))
    if top is None or not 0 <= top <= 1 or observed is None or not 0 <= now - observed <= 3600:
        unknown.append('前十原始占比缺失或快照过期')
    elif top < .9:
        reasons.append('前十原始占比不足90%')
    change = number(row.get('change24h')); hour = number((observation.get('features') or {}).get('return_1h'))
    week = number((row.get('history_features') or {}).get('return_7d'))
    history_end = number(row.get('history_candle_end'))
    if change is None or hour is None or week is None or history_end is None or history_end != int(now * 1000) // 86400000 * 86400000 - 1:
        unknown.append('过热过滤所需1小时/24小时/7日数据不足或过期')
    if (change is not None and change > 50) or (hour is not None and hour > .2) or (week is not None and week > 1):
        reasons.append('已明显拉升：1h>20%或24h>50%或7d>100%')
    symbol = row.get('futures_symbol'); amount = None
    if isinstance(oi, dict) and isinstance(premium, dict) and oi.get('symbol') == premium.get('symbol') == symbol:
        qty = number(oi.get('openInterest')); mark = number(premium.get('markPrice'))
        stamps = [number(oi.get('time')), number(premium.get('time'))]
        if qty is not None and qty >= 0 and mark is not None and mark > 0 and all(t is not None and 0 <= now * 1000 - t <= 180000 for t in stamps):
            amount = qty * mark
    if amount is None:
        unknown.append('未平仓数量/标记价缺失、过期或合约符号不符')
    elif amount < 5e6:
        reasons.append('合约未平仓名义金额不足500万美元')
    return {'address': row['address'], 'symbol': row['symbol'], 'matched': not reasons and not unknown,
            'excluded': reasons, 'unknown': unknown, 'raw_top10_share': top, 'open_interest_usd': amount,
            'quote': q, 'cleaned_concentration': 'unknown', 'control_inference': 'not_established',
            'spot_identity': 'symbol_and_official_alias_only_not_contract_level_proof'}


def record(db, results, now):
    for item in results:
        if not item['matched']:
            continue
        q = item['quote']; key = VERSION + ':' + str(int(now // 86400)) + ':' + item['address']
        db.execute('INSERT OR IGNORE INTO hypothesis_tracks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (key, item['address'], item['symbol'], now, q['price'], q['price'], q['price'], q['price'], 0,
                    q['time'], 0, '{}', json.dumps(item, ensure_ascii=False)))


def observe(db, observations, now):
    prices = {o['address']: o.get('quote') or {} for o in observations}
    for row in db.execute('SELECT * FROM hypothesis_tracks WHERE opened>?', (now - 8 * 86400,)).fetchall():
        q = prices.get(row['address'], {}); stamp = number(q.get('time')); price = number(q.get('price'))
        marks = json.loads(row['marks']); age = now - row['opened']
        for horizon in HORIZONS:
            if age > horizon + 1800 and str(horizon) not in marks:
                marks[str(horizon)] = {'missing': True}
        fresh = price is not None and price > 0 and stamp is not None and 0 <= now * 1000 - stamp <= 180000 and stamp > row['quote_time']
        if fresh:
            for horizon in HORIZONS:
                if horizon <= age <= horizon + 1800 and str(horizon) not in marks:
                    marks[str(horizon)] = {'multiple': price / row['entry'], 'at': now}
            peak = max(row['peak'], price)
            db.execute('UPDATE hypothesis_tracks SET last=?,peak=?,trough=?,drawdown=?,quote_time=?,max_gap=?,marks=? WHERE key=?',
                       (price, peak, min(row['trough'], price), max(row['drawdown'], 1 - price / peak), stamp,
                        max(row['max_gap'], (stamp - row['quote_time']) / 1000), json.dumps(marks), row['key']))
        else:
            db.execute('UPDATE hypothesis_tracks SET marks=? WHERE key=?', (json.dumps(marks), row['key']))


def cycle(db, now, get=public_json, root=Path('/var/lib/alpha-radar')):
    initialize(db)
    try:
        scan = json.loads((root / 'latest.json').read_text())
        strategy = json.loads((root / 'strategy-latest.json').read_text())
    except (OSError, ValueError):
        return {'version': VERSION, 'status': 'snapshot_unavailable'}
    with db:
        observe(db, strategy.get('observations') or [], now)
    checked = db.execute("SELECT value FROM runtime WHERE key='hypothesis_checked'").fetchone()
    if checked and now - json.loads(checked[0]) < 3600:
        return {'version': VERSION, 'status': 'not_due'}
    spot = None; premiums = {}; errors = []
    try:
        raw = get('https://api.binance.com/api/v3/exchangeInfo')
        if not isinstance(raw.get('symbols'), list) or not raw['symbols']:
            raise ValueError('spot_schema')
        # Any spot record conservatively excludes an asset, including temporarily suspended listings.
        spot = {str(r.get('baseAsset', '')).upper() for r in raw['symbols']}
    except Exception:
        errors.append('spot_directory_unavailable')
    try:
        raw = get('https://fapi.binance.com/fapi/v1/premiumIndex')
        premiums = {r['symbol']: r for r in raw} if isinstance(raw, list) else {}
    except Exception:
        errors.append('mark_prices_unavailable')
    observations = {o['address']: o for o in strategy.get('observations') or []}
    candidates = [r for r in scan.get('candidates') or [] if r['address'].lower() in observations]
    cursor_row = db.execute("SELECT value FROM runtime WHERE key='hypothesis_cursor'").fetchone()
    cursor = json.loads(cursor_row[0]) if cursor_row else 0
    candidates.sort(key=lambda r: r['address'])
    selected = (candidates[cursor:] + candidates[:cursor])[:6]
    def inspect(row):
        try:
            oi = get('https://fapi.binance.com/fapi/v1/openInterest', {'symbol': row['futures_symbol']})
        except Exception:
            oi = None
        return assess(row, observations[row['address'].lower()], spot, oi, premiums.get(row['futures_symbol']), time.time())
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(inspect, selected))
    report = {'version': VERSION, 'as_of': now, 'source': SOURCE, 'status': 'partial' if errors or any(r['unknown'] for r in results) else 'ok',
              'checked': len(results), 'universe_with_observation': len(candidates), 'errors': errors, 'rows': results,
              'notice': '独立帖子假设观察组；原始Top10高占比不能证明控盘，OI不是主力买入证据；不加买入分，不自动交易。'}
    with db:
        record(db, results, now)
        for key, value in [('hypothesis_checked', now), ('hypothesis_cursor', (cursor + len(selected)) % max(1, len(candidates))), ('alpha_hypothesis', report)]:
            db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
    return report
