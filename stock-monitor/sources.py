from __future__ import annotations
import json, math, statistics, time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote as urlquote
from net import get_json, FetchError

YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/'

def load_universe(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list): raise ValueError('stock universe must be a list')
    return data

def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

def parse_chart(raw, item):
    try:
        result = raw['chart']['result'][0]; meta = result['meta']
        quotes = result['indicators']['quote'][0]
        now = time.time(); timezone = ZoneInfo(meta['exchangeTimezoneName'])
        bars = []
        for ts, close, volume in zip(result.get('timestamp') or [], quotes.get('close') or [], quotes.get('volume') or []):
            # Keep aligned observations and exclude an unfinished 5-minute candle.
            if not all(number(v) for v in (ts, close, volume)) or close <= 0 or volume < 0 or ts + 300 > now: continue
            bars.append((ts, float(close), float(volume)))
        bars.sort()
        if not bars: raise ValueError('no_completed_bar')
        ts, price, volume = bars[-1]
        session = datetime.fromtimestamp(ts, timezone).date()
        earlier = [b for b in bars if datetime.fromtimestamp(b[0], timezone).date() < session]
        previous = earlier[-1][1] if earlier else None
        baseline_rows = [b[2] for b in bars[:-1][-20:]]
        baseline = statistics.median(baseline_rows) if len(baseline_rows) >= 5 else None
        return {**item, 'price': price, 'change': (price / previous - 1) * 100 if previous else None,
                'volume': volume, 'volume_ratio': volume / baseline if baseline and baseline > 0 else None,
                'observed_at': now, 'quote_at': ts + 300, 'fresh': 0 <= now - (ts + 300) <= 900,
                'currency': meta.get('currency'), 'exchange_timezone': str(timezone),
                'data_interval': '5m', 'data_source': 'Yahoo Finance chart',
                'change_basis': '上一交易日末根有效K线收盘价', 'volume_basis': '前20根有效5分钟K线成交量中位数（至少5根）'}
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise FetchError('invalid_chart:' + type(e).__name__) from None

class Collector:
    def __init__(self, timeout=12, spacing=0.2): self.timeout, self.spacing = timeout, spacing
    def collect(self, universe):
        rows, statuses = [], []
        for item in universe:
            symbol = item.get('symbol', '').strip()
            if not symbol: continue
            try:
                raw = get_json(YAHOO + urlquote(symbol, safe=''), self.timeout, params={'range': '5d', 'interval': '5m', 'includePrePost': 'false'})
                rows.append(parse_chart(raw, item)); statuses.append({'symbol': symbol, 'status': 'ok'})
            except Exception as e:
                statuses.append({'symbol': symbol, 'status': 'failed', 'error': type(e).__name__})
            time.sleep(self.spacing)
        return rows, statuses
