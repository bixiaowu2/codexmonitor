#!/usr/bin/env python3
"""股票雷达评估器 —— 把「信号收益」和「同期大盘收益」放在一起算。

【为什么需要】
2026-10-25 复核发现: 之前所有「股票雷达负期望」的结论**都没扣掉大盘 beta**。
实测样本窗 10-12→10-23 是普跌 —— 沪深300 -4.26% / 中证500 -3.93% / 恒生科技 -7.14%。
不扣 beta 就分不清「选股差」还是「大盘差」, 于是每次结论都在两者之间打转。
本脚本把两者并排算, 直接给「超额收益」。

【口径】
  信号收益 = forward_tracks 的 marks[h]['return']（入场价→检查点价）
  基准收益 = 同一时间窗内该市场指数日收盘的涨跌
  超额     = 信号 - 基准
  日线是近似: 起点取「≤ 事件时刻的最近一根收盘」, 终点同理。
  所以只对 24h / 7d 出基准, 1h 太细、日线量不了。

【只读】
只读 `forward_tracks` 与 `stock_matches_history.jsonl`(若存在), 不写任何生产数据。

用法:
  stock_eval.py            # 人读报告
  stock_eval.py --json     # 机器可读
"""
from __future__ import annotations
import json, sqlite3, sys, time, urllib.request
from pathlib import Path

DB = '/var/lib/stock-radar/stock-radar.sqlite'
UA = {'User-Agent': 'Mozilla/5.0'}
HORIZONS = [('24h', 86400), ('7d', 604800)]

# 市场 → 基准指数。A股=沪深300, 港股=恒生科技, 美股=纳斯达克。
BENCH = {
    'A股':  ('tencent', 'sh000300'),
    '港股': ('tencent', 'hkHSTECH'),
    '美股': ('yahoo',   '%5EIXIC'),
}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'ignore')


def _tencent_daily(code, n=120):
    """腾讯日K → [(unix_ts, close)]。"""
    import datetime
    d = json.loads(_get(f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{n},qfq'))
    node = (d.get('data') or {}).get(code) or {}
    arr = node.get('qfqday') or node.get('day') or []
    out = []
    for row in arr:
        if len(row) < 3:
            continue
        try:
            ts = datetime.datetime.strptime(row[0], '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc).timestamp()
            out.append((ts, float(row[2])))
        except (ValueError, TypeError):
            continue
    return sorted(out)


def _yahoo_daily(sym, rng='6mo'):
    """Yahoo 日线 → [(unix_ts, close)]。"""
    d = json.loads(_get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d'))
    res = d['chart']['result'][0]
    ts = res.get('timestamp') or []
    cl = (res.get('indicators', {}).get('quote') or [{}])[0].get('close') or []
    out = [(t, float(c)) for t, c in zip(ts, cl) if c]
    return sorted(out)


def load_bench(market):
    spec = BENCH.get(market)
    if not spec:
        return []
    kind, code = spec
    try:
        return _tencent_daily(code) if kind == 'tencent' else _yahoo_daily(code)
    except Exception:
        return []


def bench_return(series, t0, t1):
    """日线近似: 取 ≤t0 的最近收盘为起点, ≤t1 的最近收盘为终点。"""
    if not series:
        return None
    start = None
    for ts, c in series:
        if ts <= t0:
            start = c
        else:
            break
    end = None
    for ts, c in series:
        if ts <= t1:
            end = c
        else:
            break
    if not start or not end:
        return None
    return end / start - 1


def market_of(sym):
    s = str(sym).upper()
    if s.endswith(('.SS', '.SZ', '.BJ')):
        return 'A股'
    if s.endswith('.HK'):
        return '港股'
    if '.KS' in s:
        return '韩股'
    return '美股'


def cap_bucket(c):
    if c is None:
        return '无市值数据'
    if c < 1e9:
        return '<10亿美元'
    if c < 5e9:
        return '10-50亿美元'
    return '≥50亿美元'


def load_tracks():
    # 市值优先取 payload.features（入场时落库），缺失则回退到 universe 文件。
    # features 是 2026-10-17 才加的，早于此的 track 只能靠 universe 文件补。
    caps = {}
    for f in ('/opt/stock-radar/universe.json', '/opt/stock-radar/universe_ai_small.json'):
        try:
            items = json.loads(Path(f).read_text(encoding='utf-8'))
        except Exception:
            continue
        for it in (items if isinstance(items, list) else []):
            if isinstance(it, dict) and it.get('symbol') and it.get('cap_usd'):
                caps[it['symbol']] = it['cap_usd']

    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    out = []
    for r in con.execute('SELECT symbol, created, marks, payload FROM forward_tracks'):
        try:
            marks = json.loads(r['marks'] or '{}')
        except Exception:
            marks = {}
        try:
            payload = json.loads(r['payload'] or '{}')
        except Exception:
            payload = {}
        feats = payload.get('features') or {}
        out.append({'symbol': r['symbol'], 'created': r['created'], 'marks': marks,
                    'market': market_of(r['symbol']),
                    'cap': feats.get('cap_usd') or caps.get(r['symbol'])})
    con.close()
    return out


def mark_return(marks, label):
    m = marks.get(label)
    if not isinstance(m, dict) or m.get('missing'):
        return None
    return m.get('return')


def summarise(tracks, bench_cache):
    """返回 {维度: {分档: {horizon: {...}}}}"""
    def agg(rows):
        res = {}
        for label, secs in HORIZONS:
            sig, exc, n_bench = [], [], 0
            for t in rows:
                r = mark_return(t['marks'], label)
                if r is None:
                    continue
                sig.append(r)
                b = bench_return(bench_cache.get(t['market']) or [], t['created'], t['created'] + secs)
                if b is not None:
                    exc.append(r - b)
                    n_bench += 1
            if not sig:
                continue
            res[label] = {
                'n': len(sig),
                'signal_mean': sum(sig) / len(sig),
                'signal_winrate': sum(1 for x in sig if x > 0) / len(sig),
                'n_with_bench': n_bench,
                'excess_mean': (sum(exc) / len(exc)) if exc else None,
                'excess_winrate': (sum(1 for x in exc if x > 0) / len(exc)) if exc else None,
            }
        return res

    out = {'全部': {'全部': agg(tracks)}}
    for dim, key in (('按市场', 'market'), ('按市值', None)):
        groups = {}
        for t in tracks:
            k = t['market'] if key else cap_bucket(t['cap'])
            groups.setdefault(k, []).append(t)
        out[dim] = {k: agg(v) for k, v in sorted(groups.items())}
    return out


def fmt_pct(x):
    return '  n/a' if x is None else f'{x * 100:+6.2f}%'


def report(summary):
    print('=' * 78)
    print('股票雷达评估 —— 信号收益 vs 同期大盘（日线近似）')
    print('=' * 78)
    for dim, groups in summary.items():
        print(f'\n【{dim}】')
        print(f"  {'分档':<14}{'窗口':<6}{'n':>5}{'信号均值':>10}{'信号胜率':>10}{'超额均值':>10}{'超额胜率':>10}")
        for gname, hs in groups.items():
            for label, _ in HORIZONS:
                s = hs.get(label)
                if not s:
                    continue
                print(f"  {gname:<14}{label:<6}{s['n']:>5}{fmt_pct(s['signal_mean']):>10}"
                      f"{fmt_pct(s['signal_winrate']):>10}{fmt_pct(s['excess_mean']):>10}"
                      f"{fmt_pct(s['excess_winrate']):>10}")
    print('\n注: 「超额」= 信号收益 − 同期该市场指数收益。n_with_bench 不足时超额为空。')


def main():
    tracks = load_tracks()
    bench_cache = {m: load_bench(m) for m in ('A股', '港股', '美股')}
    summary = summarise(tracks, bench_cache)
    if '--json' in sys.argv:
        print(json.dumps({'as_of': time.time(), 'tracks': len(tracks), 'summary': summary},
                         ensure_ascii=False, indent=2))
    else:
        report(summary)


if __name__ == '__main__':
    main()
