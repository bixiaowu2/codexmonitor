from __future__ import annotations
import urllib.request
import json, math, statistics, time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote as urlquote
from net import get_json, FetchError

YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/'

# ---------------- 腾讯行情兜底(Yahoo 失败时用) ----------------
TENCENT_SNAP = 'https://qt.gtimg.cn/q='
TENCENT_M5 = 'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param='


def tencent_code(symbol):
    """雷达符号 → 腾讯代码。A股 sh600519/sz000001, 港股 hk00700, 美股 usAAPL"""
    sym = str(symbol or '').strip()
    up = sym.upper()
    if up.endswith('.SS'):
        return 'sh' + sym.split('.')[0]
    if up.endswith('.SZ'):
        return 'sz' + sym.split('.')[0]
    if up.endswith('.BJ'):
        return 'bj' + sym.split('.')[0]
    if up.endswith('.HK'):
        return 'hk' + sym.split('.')[0].zfill(5)
    return 'us' + up                      # 美股: usAAPL


def _http(url, timeout=10):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def tencent_snapshot(symbol, item):
    """腾讯快照 —— 给 price/change/成交量/时间戳(无 5m 序列, 故量比 None)。"""
    code = tencent_code(symbol)
    raw = _http(TENCENT_SNAP + code).decode('gbk', 'ignore')
    if '="' not in raw:
        return None
    f = raw.split('="')[1].split('~')
    if len(f) < 36:
        return None
    def num(i):
        try:
            return float(f[i])
        except (ValueError, IndexError):
            return None
    price, prev = num(3), num(4)
    if not price or price <= 0:
        return None
    now = time.time()
    return {**item, 'price': price, 'change': num(32),
            'volume': num(6), 'volume_ratio': None,
            'observed_at': now, 'quote_at': now, 'quote_age_seconds': 0,
            'fresh_reason': 'tencent_snapshot', 'fresh': True,
            'currency': f[35].strip() if len(f) > 35 else None,
            'data_interval': 'snapshot', 'data_source': 'Tencent 腾讯行情(兜底)',
            'change_basis': '腾讯给出的涨跌幅(较昨收)', 'volume_basis': '腾讯成交量(无量比基准)'}


def tencent_m5(symbol, item):
    """腾讯 5 分钟K线兜底 —— **只有 A股有效**(实测港股/美股返回 0 根)。
    与 Yahoo 同口径: change=较上一交易日末根收盘, volume_ratio=现量/前20根中位。
    """
    code = tencent_code(symbol)
    try:
        raw = json.loads(_http(TENCENT_M5 + code + ',m5,,320').decode('utf-8', 'ignore'))
    except Exception:
        return None
    node = raw.get('data') or {}
    rows = None
    for v in node.values():
        if isinstance(v, dict) and isinstance(v.get('m5'), list) and v['m5']:
            rows = v['m5']
            break
    if not rows:
        return None
    now = time.time()
    bars = []
    for r in rows:
        try:                                  # [时间(YYYYMMDDHHMM), 开, 收, 高, 低, 量, {}, 均价]
            ts = time.mktime(time.strptime(str(r[0]), '%Y%m%d%H%M'))
            close, vol = float(r[2]), float(r[5])
        except (ValueError, IndexError, TypeError):
            continue
        if close <= 0 or ts + 300 > now + 3600:   # 排除未完成的当前根(留时区余量)
            continue
        bars.append((ts, close, vol))
    if not bars:
        return None
    bars.sort()
    ts, price, volume = bars[-1]
    session = time.strftime('%Y%m%d', time.localtime(ts))
    earlier = [b for b in bars if time.strftime('%Y%m%d', time.localtime(b[0])) < session]
    previous = earlier[-1][1] if earlier else None
    base_rows = [b[2] for b in bars[:-1][-20:]]
    base = sorted(base_rows)[len(base_rows) // 2] if len(base_rows) >= 5 else None
    age = now - (ts + 300)
    return {**item, 'price': price, 'change': (price / previous - 1) * 100 if previous else None,
            'volume': volume, 'volume_ratio': volume / base if base and base > 0 else None,
            'observed_at': now, 'quote_at': ts + 300, 'quote_age_seconds': age,
            'fresh_reason': 'fresh' if 0 <= age <= 900 else 'quote_stale_or_market_closed',
            'fresh': 0 <= age <= 900,
            'currency': item.get('currency'),
            'data_interval': '5m', 'data_source': 'Tencent 腾讯5分钟K线(兜底)',
            'change_basis': '上一交易日末根有效K线收盘价',
            'volume_basis': '前20根有效5分钟K线成交量中位数（至少5根）'}


def load_universe(path):
    """path 可以是逗号分隔的多个 universe 文件(多池并行), 合并为一个列表并按 symbol 去重。"""
    items, seen = [], set()
    for part in str(path).split(','):
        part = part.strip()
        if not part:
            continue
        data = json.loads(Path(part).read_text())
        if not isinstance(data, list): raise ValueError('stock universe must be a list')
        for it in data:
            sym = it.get('symbol')
            if sym and sym not in seen:
                seen.add(sym)
                items.append(it)
    return items

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
        quote_at = ts + 300
        age = now - quote_at
        if 0 <= age <= 900:
            freshness_reason = 'fresh'
        elif datetime.fromtimestamp(now, timezone).weekday() >= 5:
            freshness_reason = 'market_closed_weekend'
        else:
            freshness_reason = 'quote_stale_or_market_closed'
        return {**item, 'price': price, 'change': (price / previous - 1) * 100 if previous else None,
                'volume': volume, 'volume_ratio': volume / baseline if baseline and baseline > 0 else None,
                'observed_at': now, 'quote_at': quote_at, 'quote_age_seconds': age,
                'fresh_reason': freshness_reason, 'fresh': freshness_reason == 'fresh',
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
                # Yahoo 失败 → 腾讯兜底。A股 用 5分钟K线(含量比), 其它市场退到快照(量比 None)。
                fallback = None
                for fn in (tencent_m5, tencent_snapshot):
                    try:
                        fallback = fn(symbol, item)
                    except Exception:
                        fallback = None
                    if fallback:
                        break
                if fallback:
                    rows.append(fallback)
                    statuses.append({'symbol': symbol, 'status': 'ok_fallback',
                                     'via': fallback.get('data_source'), 'yahoo_error': type(e).__name__})
                else:
                    statuses.append({'symbol': symbol, 'status': 'failed', 'error': type(e).__name__})
            time.sleep(self.spacing)
        return rows, statuses
