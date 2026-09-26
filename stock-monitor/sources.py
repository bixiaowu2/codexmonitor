from __future__ import annotations
import json, time
from pathlib import Path
from net import get_json, FetchError

YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/'

def load_universe(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list): raise ValueError('stock universe must be a list')
    return data

def parse_chart(raw, item):
    try:
        result = raw['chart']['result'][0]; meta = result['meta']; ts = result.get('timestamp') or []
        quote = (result.get('indicators', {}).get('quote') or [{}])[0]
        closes = [x for x in quote.get('close', []) if isinstance(x, (int, float))]
        volumes = [x for x in quote.get('volume', []) if isinstance(x, (int, float))]
        if not closes: raise ValueError('no_close')
        last = float(closes[-1]); prev = float(closes[-2]) if len(closes) > 1 else last
        median_vol = sorted(volumes[:-1])[-max(1, min(20, len(volumes)-1)):] if len(volumes) > 1 else []
        baseline = sorted(median_vol)[len(median_vol)//2] if median_vol else 0
        return {**item, 'price': last, 'change': (last / prev - 1) * 100 if prev else 0,
                'volume': float(volumes[-1]) if volumes else 0, 'volume_ratio': (float(volumes[-1]) / baseline if baseline else 0),
                'observed_at': time.time(), 'currency': meta.get('currency'), 'market_state': meta.get('marketState'),
                'data_source': 'Yahoo Finance chart'}
    except (KeyError, IndexError, TypeError, ValueError) as e: raise FetchError('invalid_chart:' + type(e).__name__) from e

class Collector:
    def __init__(self, timeout=12, spacing=0.2): self.timeout, self.spacing = timeout, spacing
    def collect(self, universe):
        rows, statuses = [], []
        for item in universe:
            symbol = item.get('symbol', '').strip()
            if not symbol: continue
            try:
                raw = get_json(YAHOO + symbol, self.timeout, params={'range': '1mo', 'interval': '1d', 'events': 'div,splits'})
                rows.append(parse_chart(raw, item)); statuses.append({'symbol': symbol, 'status': 'ok'})
            except Exception as e:
                statuses.append({'symbol': symbol, 'status': 'failed', 'error': str(e)[:80]})
            time.sleep(self.spacing)
        return rows, statuses
