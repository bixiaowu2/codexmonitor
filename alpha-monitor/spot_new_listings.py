"""现货新上市回溯（exchangeInfo-diff 口径）。

由 demon_scan.py 在拿完 spot exchangeInfo 之后调用；本模块不新增数据源、不独立运行。

口径（lead 4081 核定）：
- 差集 = 本轮 TRADING baseAsset - 上一轮落盘集合；首轮只落盘、不产假新上市
- 每标的 1 次 klines（1d limit=200），open/d1/d3/d7/d30/d90 全由这一份算，不足写 None
- 每轮最多回溯 20 个，超出进 pending，下一轮优先
- 并发 3；429 / code=-1003 立即停本轮剩余；单轮失败率 >30% 停本轮剩余
- 输出 spot_new_listings.json（独立文件，不碰 matches.json）+ append-only spot_listing_log.jsonl
- 不改 timer/unit、不改现有筛选语义、不碰公告 API
"""
from __future__ import annotations
import json
import subprocess
import time
import concurrent.futures
from pathlib import Path

DATA = Path('/var/lib/demon-radar')
STATE = DATA / 'spot_bases_seen.json'
OUT = DATA / 'spot_new_listings.json'
LOG = DATA / 'spot_listing_log.jsonl'

MAX_PER_ROUND = 20
WORKERS = 3
FAIL_STOP_RATIO = 0.30
HORIZONS = (1, 3, 7, 30, 90)
KLINE_URL = 'https://api.binance.com/api/v3/klines'


class RateLimited(Exception):
    pass


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def fetch_klines(base):
    """1 标的一次请求 + 2 次重试（退避 1s/2s，与 radar.PublicAPI 一致）。

    自己发 curl 而不用 PublicAPI，是为了留下 http_code：PublicAPI 把 429 折成 curl=22，
    认不出 -1003，无法按 lead 核定「遇 429 立即停本轮」处理。
    """
    symbol = base + 'USDT'
    url = '%s?symbol=%s&interval=1d&limit=200' % (KLINE_URL, symbol)
    last = ''
    for attempt in range(3):
        p = subprocess.run(['curl', '--compressed', '--silent', '--show-error',
                            '--connect-timeout', '10', '--max-time', '30',
                            '-w', '\n%{http_code}', url],
                           capture_output=True, timeout=35)
        parts = p.stdout.rsplit(b'\n', 1)
        http = parts[-1].strip() if len(parts) == 2 else b''
        body = parts[0] if len(parts) == 2 else p.stdout
        if http in (b'429', b'418') or b'"code":-1003' in body:
            raise RateLimited('%s: http=%s' % (symbol, http.decode(errors='replace')))
        if http in (b'200', b'400'):
            try:
                payload = json.loads(body)
            except (ValueError, TypeError) as e:
                last = 'json: %s' % e
            else:
                # -1121 Invalid symbol：BASEUSDT 在现货不存在，按方案记 no_binance_spot_pair，不猜别名
                if isinstance(payload, dict) and payload.get('code') == -1121:
                    return {'status': 'no_binance_spot_pair', 'symbol': symbol, 'closes': []}
                if http == b'200' and isinstance(payload, list) and payload:
                    closes = [float(k[4]) for k in payload if isinstance(k, list) and len(k) >= 5]
                    return {'status': 'ok', 'symbol': symbol, 'closes': closes,
                            'kline_open_time': payload[0][0], 'open': closes[0] if closes else None}
                last = 'empty payload'
        else:
            last = 'http=%s curl=%s %s' % (http.decode(errors='replace'), p.returncode,
                                           p.stderr.decode(errors='replace')[:120])
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise RuntimeError('%s: %s' % (symbol, last))


def _levels(closes):
    return {'d%d' % n: (closes[n - 1] if len(closes) >= n else None) for n in HORIZONS}


def _tiers():
    """新上市清单本身不产生候选：tier 只在标的已出现在 matches.json 时沿用，否则 None。"""
    rows = _read_json(DATA / 'matches.json', [])
    return {r.get('futures') or r.get('symbol'): r.get('tier') for r in rows if isinstance(r, dict)}


def _rewrite(rows=None):
    """补算与落盘：只用已存的 closes 重算 d1..d90，零请求。"""
    if rows is None:
        rows = {r.get('base'): r for r in _read_json(OUT, []) if isinstance(r, dict) and r.get('base')}
    tiers = _tiers()
    out = []
    for base in sorted(rows):
        r = dict(rows[base])
        closes = r.pop('closes', []) or []
        if r.get('status') == 'ok' and closes:
            if r.get('open') is None:
                r['open'] = closes[0]
            r.update(_levels(closes))
        if not r.get('tier'):
            r['tier'] = tiers.get(r.get('symbol'))
        out.append(r)
    _dump(OUT, out)


def run(spot_payload, now_utc):
    """spot_payload = demon_scan 那份 spot exchangeInfo 原文，零额外请求。"""
    t0 = time.time()
    symbols = spot_payload.get('symbols', []) if isinstance(spot_payload, dict) else []
    current = {str(s.get('baseAsset', '')).upper() for s in symbols if s.get('status') == 'TRADING'}
    current.discard('')

    state = _read_json(STATE, None)
    if not isinstance(state, dict) or 'bases' not in state:
        _dump(STATE, {'last_run_utc': now_utc, 'bases': sorted(current), 'pending': []})
        _rewrite()
        print('spot-new-listings: bootstrap %d bases, no diff this round' % len(current), flush=True)
        return {'new': 0, 'backlog': 0, 'err': 0, 'cost_s': round(time.time() - t0, 1),
                'bootstrap': True}

    seen = {str(b).upper() for b in state.get('bases', [])}
    pending = [str(b).upper() for b in state.get('pending', [])]
    fresh = [b for b in sorted(current - seen) if b not in pending]
    queue = pending + fresh
    batch, rest = queue[:MAX_PER_ROUND], queue[MAX_PER_ROUND:]

    # 新 base 立即并入 seen，避免下一轮重复判新；未回溯的进 pending 排队
    state['bases'] = sorted(seen | current)
    state['last_run_utc'] = now_utc

    if not batch:
        state['pending'] = rest
        _dump(STATE, state)
        _rewrite()
        print('spot-new-listings: new=0 backlog=0 err=0 cost_s=%s'
              % round(time.time() - t0, 1), flush=True)
        return {'new': len(fresh), 'backlog': 0, 'err': 0,
                'cost_s': round(time.time() - t0, 1), 'bootstrap': False}

    abort = {'stop': False}

    def work(base):
        if abort['stop']:
            return base, None, 'skipped'
        try:
            return base, fetch_klines(base), None
        except RateLimited as e:
            abort['stop'] = True
            return base, None, 'rate_limited:%s' % e
        except Exception as e:
            return base, None, 'err:%s' % str(e)[:120]

    got, retry_later, fails, attempted = {}, [], 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(work, b) for b in batch]
        for fut in concurrent.futures.as_completed(futures):
            base, res, err = fut.result()
            if err == 'skipped':
                retry_later.append(base)
                continue
            attempted += 1
            if err:
                fails += 1
                retry_later.append(base)
                if err.startswith('rate_limited') or fails / attempted > FAIL_STOP_RATIO:
                    abort['stop'] = True
                continue
            got[base] = res

    rows = {r.get('base'): r for r in _read_json(OUT, []) if isinstance(r, dict) and r.get('base')}
    stamp = now_utc
    log_lines = []
    for base, res in got.items():
        rows[base] = {'symbol': res['symbol'], 'base': base, 'first_seen_utc': stamp,
                      'kline_open_time': res.get('kline_open_time'), 'open': res.get('open'),
                      'status': res['status'], 'closes': res.get('closes', [])}
        log_lines.append({'base': base, 'symbol': res['symbol'], 'first_seen_utc': stamp,
                          'status': res['status'], 'kline_open_time': res.get('kline_open_time')})
    _rewrite(rows)

    if log_lines:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as fh:
            for ln in log_lines:
                fh.write(json.dumps(ln, ensure_ascii=False) + '\n')

    state['pending'] = rest + retry_later
    _dump(STATE, state)
    summary = {'new': len(fresh), 'backlog': len(batch) - len(got), 'err': fails,
               'cost_s': round(time.time() - t0, 1), 'bootstrap': False}
    print('spot-new-listings: new=%(new)d backlog=%(backlog)d err=%(err)d cost_s=%(cost_s)s' % summary,
          flush=True)
    return summary
