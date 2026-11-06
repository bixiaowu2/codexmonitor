"""规则1命中标记 (ca0br 2026-11-06 裁定)：只记录、不交易、不改选币/阈值。

规则1: buy_ratio >= 0.6 且流动性 $10k-$20k。
来源池: forward_tracks（雷达跟踪池，双风格），按 instrument_key 取最新一条去重。
风格: source=meme_rush -> bn；geckoterminal/dexscreener/gmgn -> gecko；
source 缺失时按特征签名判：含 protocol/countBuy/migrateStatus/bnHoldingPercent -> bn，
含 pool_created_at -> gecko，否则 unknown。
输出: rule1_marks.jsonl（同目录），每行一条命中，(chain,address) 去重追加；
不重复记同一标的。只读原表，不改任何评分/选币/阈值。
用法: python3 /opt/alpha-radar/rule1_mark.py
"""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path

DB = Path('/var/lib/meme-radar/meme-radar.sqlite')
OUT = Path(__file__).resolve().parent / 'rule1_marks.jsonl'

STYLE = {'meme_rush': 'bn', 'geckoterminal': 'gecko', 'dexscreener': 'gecko', 'gmgn': 'gecko'}

def buy_ratio(f):
    br = f.get('buy_ratio_1h')
    if isinstance(br, (int, float)):
        return float(br)
    cb, cs = f.get('countBuy'), f.get('countSell')
    if isinstance(cb, (int, float)) and isinstance(cs, (int, float)) and (cb + cs) > 0:
        return cb / (cb + cs)
    b, s = f.get('buys_1h'), f.get('sells_1h')
    if isinstance(b, (int, float)) and isinstance(s, (int, float)) and (b + s) > 0:
        return b / (b + s)
    return None

def classify_style(f):
    src = f.get('source')
    if src in STYLE:
        return STYLE[src], str(src)
    if any(k in f for k in ('protocol', 'countBuy', 'migrateStatus', 'bnHoldingPercent')):
        return 'bn', 'meme_rush(signature)'
    if 'pool_created_at' in f:
        return 'gecko', 'radar(signature)'
    return 'unknown', 'unknown'

def main():
    now = time.time()
    seen = set()
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            try:
                e = json.loads(line)
                seen.add((e.get('chain'), (e.get('address') or '').lower()))
            except ValueError:
                pass
    db = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    rows = db.execute('SELECT instrument_key, symbol, market, created, payload FROM forward_tracks ORDER BY created DESC').fetchall()
    latest = {}
    for ik, sym, mkt, created, pl in rows:
        if ik in latest:
            continue
        latest[ik] = (sym, mkt, created, json.loads(pl))
    hits, skipped = [], 0
    for ik, (sym, mkt, created, p) in latest.items():
        f = p.get('features', {}) or {}
        br = buy_ratio(f)
        liq = f.get('liquidity_usd')
        if liq is None:
            liq = f.get('liquidity')
        if br is None or liq is None:
            skipped += 1
            continue
        if br >= 0.6 and 10000 <= float(liq) <= 20000:
            parts = ik.split(':')
            addr = parts[1] if len(parts) >= 2 else ''
            key = (mkt, addr.lower())
            if key in seen:
                continue
            style, src = classify_style(f)
            sc = (p.get('score') or {}).get('score')
            hits.append({'marked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                         'chain': mkt, 'address': addr, 'symbol': sym,
                         'buy_ratio': round(float(br), 4), 'liquidity_usd': round(float(liq), 2),
                         'style': style, 'source': src, 'score': sc,
                         'forward_key': ik, 'rule': 'rule1'})
    with OUT.open('a') as fh:
        for h in hits:
            fh.write(json.dumps(h, ensure_ascii=False) + '\n')
    total = sum(1 for _ in OUT.read_text().splitlines() if _.strip()) if OUT.exists() else 0
    print(json.dumps({'new_hits': len(hits), 'total_marked': total,
                      'pool_latest': len(latest), 'no_buy_ratio_or_liq': skipped,
                      'by_style': {s: sum(1 for h in hits if h['style'] == s) for s in ('gecko', 'bn', 'unknown')}}, ensure_ascii=False))

if __name__ == '__main__':
    main()
