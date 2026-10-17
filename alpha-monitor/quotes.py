"""Alpha spot quotes with explicit source time; never refresh a price by fetching it."""
import time
from radar import ALPHA, num
from binance_official import ticker_quote

MAX_AGE_MS = 180000

# Reasons callers can use to distinguish data-quality states without inventing a price.
#  - ticker_unavailable_or_invalid: ticker request itself failed (network, schema, 5xx).
#  - ticker_stale_or_future: ticker responded but its lastPrice is older than MAX_AGE_MS
#    or the closeTime is in the future, so it is unusable as a current quote.
#  - ticker_stale_no_recent_trade: ticker responded and a 1m kline fallback was attempted
#    but produced no traded minute inside the freshness window.  This is a real-market
#    condition (the token has not traded recently), not an interface failure.  It must
#    not be counted as a scan/partial error for non-position candidates.
#  - minute_data_unavailable: 1m kline request itself failed; this is an interface error.
#  - no_recent_traded_minute: 1m kline returned but contained no traded candle in window.
REASON_TICKER_INVALID = 'ticker_unavailable_or_invalid'
REASON_TICKER_STALE = 'ticker_stale_or_future'
REASON_NO_RECENT_TRADE = 'ticker_stale_no_recent_trade'
REASON_KLINE_INVALID = 'minute_data_unavailable'
REASON_NO_TRADED_MINUTE = 'no_recent_traded_minute'


def valid(quote, now):
    return bool(quote and num(quote.get('price')) is not None and quote['price'] > 0
                and num(quote.get('time')) is not None
                and -60000 <= now * 1000 - quote['time'] <= MAX_AGE_MS)

def minute_quote(rows, now):
    candidates = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, list) or len(row) < 9:
            continue
        values = [num(row[i]) for i in (0, 1, 2, 3, 4, 5, 6, 7, 8)]
        if any(v is None for v in values):
            continue
        start, opened, high, low, close, volume, end, turnover, trades = values
        # A zero-trade candle carries an old price forward. Its end is NOT a trade time.
        if (end - start != 59999 or start > now * 1000 or volume <= 0 or turnover <= 0
                or trades < 1 or min(opened, high, low, close) <= 0
                or not low <= min(opened, close) <= max(opened, close) <= high):
            continue
        # The trade occurred within this minute. Use the start as a conservative lower bound.
        q = {'price': close, 'time': start, 'source': 'alpha_1m_traded',
             'time_basis': 'minute_start_lower_bound'}
        if valid(q, now):
            candidates.append(q)
    return max(candidates, key=lambda q: q['time']) if candidates else None

def get_quote(fetch, alpha_id, fallback=False):
    """Bounded public requests; callable fetch owns HTTP timeouts. No guessed ticker symbols."""
    symbol = alpha_id + 'USDT'
    diagnostic = {'symbol': symbol, 'reason': 'quote_unavailable'}
    ticker_failed = False
    try:
        raw = fetch(ALPHA + '/ticker', {'symbol': symbol})
        # Binance's documented Alpha response has appeared both as a flat
        # object and under data/list.  Normalize it before the legacy parser.
        official, official_detail = ticker_quote(raw, alpha_id, time.time(), MAX_AGE_MS)
        if official:
            official['source'] = 'alpha_ticker'
            diagnostic.update(official_detail, source='alpha_ticker', reason='ok')
            return official, diagnostic
        if not isinstance(raw, dict) or raw.get('symbol', symbol) != symbol:
            raise ValueError('schema_or_symbol')
        price, stamp = num(raw.get('lastPrice')), num(raw.get('closeTime'))
        if price is None or price <= 0 or stamp is None:
            raise ValueError('schema_or_price')
        diagnostic.update(last_quote_time=stamp, age_seconds=round(time.time()-stamp/1000, 1))
        quote = {'price': price, 'time': stamp, 'source': 'alpha_ticker',
                 'provider': 'binance_alpha_official'}
        if valid(quote, time.time()):
            return quote, dict(diagnostic, reason='ok', source=quote['source'])
        diagnostic['reason'] = REASON_TICKER_STALE
    except Exception:
        ticker_failed = True
        diagnostic['reason'] = REASON_TICKER_INVALID
    if fallback:
        try:
            q = minute_quote(fetch(ALPHA + '/klines', {'symbol': symbol, 'interval': '1m', 'limit': 4}), time.time())
            if q:
                return q, dict(diagnostic, reason='ok', source=q['source'], last_quote_time=q['time'],
                               age_seconds=round(time.time()-q['time']/1000, 1))
            # Ticker responded but no recent 1m traded candle: a real-market condition.
            diagnostic['fallback'] = REASON_NO_TRADED_MINUTE
            if not ticker_failed:
                diagnostic['reason'] = REASON_NO_RECENT_TRADE
        except Exception:
            diagnostic['fallback'] = REASON_KLINE_INVALID
    return None, diagnostic
