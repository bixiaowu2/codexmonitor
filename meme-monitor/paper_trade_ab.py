"""A/B 实验臂 H2 —— 独立台账，不混账(mark=ab_h2_v1)。

裁定：lead 2026-11-07T0945Z-8851 / home 2026-11-07T101606Z-78fa
  独立文件 paper_trade_ab.sqlite；不新增 timer/unit，由 paper_trade.py 同进程周期调用
  (实验臂与对照臂同一时刻取价)。
入场：liq $10–25K + 池龄 5–15 分 + 多源 ≥2 + 含 dex；出场规则与对照臂一致。
  池龄 = min(fetched_at) − created_at（H2 预登记第二节；lead 2026-11-07T1137Z-8c4e 确认修 bug 非规则变更）。
  多源 ≥2（lead 2026-11-07T1129Z 更正，撤回 110644Z-c6ae 的 ≥1 放宽）。首见查找窗口 30h，候选宇宙仍 24h。
判据：峰值≥10x 笔数 / 均值 / 归零率，跑满 150 笔再判；与 forward_tracks 分开统计。
"""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path

DATA = Path('/var/lib/meme-radar')
AB_DB = DATA / 'paper_trade_ab.sqlite'
RADAR_DB = DATA / 'meme-radar.sqlite'
ARM, MARK = 'H2', 'ab_h2_v1'
STOP_PCT, TP1, TP2, TRAIL_PCT, MAX_HOLD_DAYS = 0.50, 2.0, 5.0, 0.30, 7
AB_LIQ_MIN, AB_LIQ_MAX, AB_AGE_MIN, AB_AGE_MAX = 10000, 25000, 5, 15
LOOKBACK_H = 24
FIRST_SEEN_H = 30


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS positions(
        key TEXT PRIMARY KEY, arm TEXT, mark TEXT, symbol TEXT, chain TEXT, address TEXT,
        entry REAL, opened REAL, peak REAL, stop REAL,
        tp1_done INT DEFAULT 0, tp2_done INT DEFAULT 0, closed INT DEFAULT 0,
        realized REAL DEFAULT 0, last_price REAL, last_at REAL, note TEXT,
        closed_at REAL, liquidity_usd REAL, market_cap REAL, score REAL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS fills(
        key TEXT, arm TEXT, at REAL, kind TEXT, price REAL, mult REAL, pnl REAL)''')
    db.execute('CREATE INDEX IF NOT EXISTS ab_fills_by_key ON fills(key, at)')
    db.execute('''CREATE TABLE IF NOT EXISTS price_marks(
        key TEXT, arm TEXT, at REAL, price REAL, mult REAL)''')
    db.execute('CREATE INDEX IF NOT EXISTS ab_marks_by_key ON price_marks(key, at)')


def _token_meta(db, now, lookback_h=LOOKBACK_H):
    """按标的聚合：首见 fetched_at、source 集合、是否出现过 dex。多源与含 dex 按标的判（单行 payload 只有单个 source）。"""
    """H2 预登记：池龄 = 该标的 signals 里最早一条 fetched_at − 其 created_at。
    首见查找窗口单独放宽到 30h（lead 2026-11-07T1137Z-8c4e 批准），candidates 宇宙仍 24h。"""
    ix = {}; src = {}; dex = set()
    for _c, payload in db.execute('SELECT created, payload FROM signals WHERE created >= ?',
                                  (now - max(lookback_h, FIRST_SEEN_H) * 3600,)):
        try:
            d = json.loads(payload)
        except Exception:
            continue
        k = (d.get('chain'), str(d.get('address', '')).lower())
        ft = d.get('fetched_at')
        if ft is None:
            continue
        try:
            ft = float(ft)
        except (TypeError, ValueError):
            continue
        if k not in ix or ft < ix[k]:
            ix[k] = ft
        if d.get('source'):
            src.setdefault(k, set()).add(str(d.get('source')))
        if d.get('dex'):
            dex.add(k)
    return ix, src, dex


def _age_min(d, ix):
    """池龄（分钟）= min(fetched_at) − created_at，符合 H2 预登记第二节。"""
    if not ix:
        return None
    ft = ix.get((d.get('chain'), str(d.get('address', '')).lower()))
    c = d.get('created_at')
    if ft is None or not c:
        return None
    c = float(c); c = c / 1000 if c > 1e10 else c
    return max(0.0, (ft - c) / 60)


def _sources(d):
    s = set()
    for x in (d.get('mention_sources') or []):
        s.add('m:' + str(x)[:60])
    for x in (d.get('smart_wallet_sources') or []):
        s.add('w:' + str(x)[:60])
    if (d.get('x_hits') or 0) > 0: s.add('x')
    if (d.get('kol_hits') or 0) > 0: s.add('k')
    if (d.get('smart_wallet_hits') or 0) > 0: s.add('s')
    return s


def candidates(now):
    out = []
    try:
        db = sqlite3.connect(f'file:{RADAR_DB}?mode=ro', uri=True, timeout=15)
        rows = db.execute('SELECT created, payload FROM signals WHERE created >= ?',
                          (now - LOOKBACK_H * 3600,)).fetchall()
        ix, src, dex = _token_meta(db, now)
        db.close()
    except Exception as e:
        print(f'AB臂: 读 signals 失败 {e}')
        return out
    for _c, payload in rows:
        try:
            d = json.loads(payload)
        except Exception:
            continue
        liq = d.get('liquidity_usd') or 0
        if not (AB_LIQ_MIN <= liq <= AB_LIQ_MAX): continue
        age = _age_min(d, ix)
        if age is None or not (AB_AGE_MIN <= age <= AB_AGE_MAX): continue
        _k = (d.get('chain'), str(d.get('address', '')).lower())
        # 多源门槛恢复 ≥2（lead 2026-11-07T1129Z 更正撤回 c6ae 的 ≥1 放宽）；按标的聚合 source
        if len(src.get(_k, set()) | _sources(d)) < 2: continue
        if not d.get('dex') and _k not in dex: continue
        if not d.get('address') or not (d.get('price_usd') or 0): continue
        out.append(d)
    return out


def price_of(c, matches):
    """与对照臂同口径取价：优先最新扫描 price_usd，掉出候选走 DexScreener 兜底。"""
    row = matches.get((c.get('chain'), str(c.get('address', '')).lower()))
    if row and row.get('price_usd'):
        try:
            return float(row['price_usd'])
        except (TypeError, ValueError):
            pass
    try:
        import sources
        url = f"{sources.DEX}/tokens/v1/{c.get('chain')}/{c.get('address')}"
        data = sources.get_json(url, 10)
        if isinstance(data, list) and data:
            best = max(data, key=lambda r: float((r.get('liquidity') or {}).get('usd') or 0))
            v = float(best.get('priceUsd') or 0)
            return v or None
    except Exception:
        pass
    return None


def run(matches, now=None, verbose=True):
    now = now or time.time()
    db = sqlite3.connect(str(AB_DB), timeout=15)
    db.row_factory = sqlite3.Row
    init(db)
    opened = closed = 0
    for d in candidates(now):
        key = f"H2|{d.get('chain')}:{str(d.get('address', '')).lower()}"
        prev = db.execute('SELECT closed, closed_at FROM positions WHERE key=?', (key,)).fetchone()
        if prev is not None:
            if not prev['closed']: continue
            if prev['closed_at'] is not None and now - prev['closed_at'] < 24 * 3600: continue
            db.execute('DELETE FROM positions WHERE key=?', (key,))
        entry = float(d.get('price_usd') or 0)
        if entry <= 0: continue
        db.execute('INSERT INTO positions(key,arm,mark,symbol,chain,address,entry,opened,peak,stop,'
                   'last_price,last_at,liquidity_usd,market_cap,score)'
                   ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (key, ARM, MARK, d.get('symbol'), d.get('chain'), d.get('address'),
                    entry, now, entry, entry * (1 - STOP_PCT), entry, now,
                    d.get('liquidity_usd'), d.get('market_cap'),
                    (d.get('score_meta') or {}).get('score')))
        opened += 1
    for p in db.execute('SELECT * FROM positions WHERE closed=0').fetchall():
        entry = p['entry'] or 0
        price = price_of({'chain': p['chain'], 'address': p['address']}, matches)
        if not price:
            if entry > 0 and now - p['opened'] >= MAX_HOLD_DAYS * 86400:
                last = p['last_price'] or entry
                left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
                if left > 0 and last:
                    m = last / entry
                    db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?)',
                               (p['key'], ARM, now, 'timeout_nopx', last, m, left * (m - 1)))
                    db.execute('UPDATE positions SET realized=realized+? WHERE key=?',
                               (left * (m - 1), p['key']))
                db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                           (now, '持有到期(无行情)', p['key']))
                closed += 1
            continue
        if entry <= 0: continue
        peak = max(p['peak'], price)
        db.execute('UPDATE positions SET peak=?, last_price=?, last_at=? WHERE key=?',
                   (peak, price, now, p['key']))
        mult = price / entry
        db.execute('INSERT INTO price_marks VALUES(?,?,?,?,?)', (p['key'], ARM, now, price, mult))

        def close_part(kind, frac, px):
            m = px / entry; pnl = frac * (m - 1)
            db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?)',
                       (p['key'], ARM, now, kind, px, m, pnl))
            db.execute('UPDATE positions SET realized=realized+? WHERE key=?', (pnl, p['key']))

        if price <= p['stop']:
            close_part('stop', 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0, price)
            db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                       (now, '止损', p['key']))
            closed += 1; continue
        if not p['tp1_done'] and mult >= TP1:
            close_part('tp1', 1/3, entry * TP1)
            db.execute('UPDATE positions SET tp1_done=1 WHERE key=?', (p['key'],))
        if not p['tp2_done'] and mult >= TP2:
            close_part('tp2', 1/3, entry * TP2)
            db.execute('UPDATE positions SET tp2_done=1 WHERE key=?', (p['key'],))
        if peak > entry and price <= peak * (1 - TRAIL_PCT):
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0:
                close_part('trail', left, price)
                db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                           (now, '移动止盈', p['key']))
                closed += 1; continue
        if now - p['opened'] >= MAX_HOLD_DAYS * 86400:
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0: close_part('timeout', left, price)
            db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                       (now, '持有到期', p['key']))
            closed += 1
    db.commit()
    if verbose:
        tot = db.execute('SELECT COUNT(*) FROM positions').fetchone()[0]
        print(f'实验臂 H2({MARK}): 新开仓 {opened} | 平仓 {closed} | 台账共 {tot} 笔')
    db.close()
    return opened, closed


def report():
    db = sqlite3.connect(str(AB_DB), timeout=15)
    db.row_factory = sqlite3.Row
    init(db)
    rows = db.execute('SELECT * FROM positions').fetchall()
    closed = [r for r in rows if r['closed']]
    print('=' * 72)
    print(f'A/B 实验臂 H2({MARK}): 共 {len(rows)} 笔 | 已平仓 {len(closed)} | 持仓中 {len(rows)-len(closed)}')
    if closed:
        v = sorted(r['realized'] for r in closed)
        ge10 = sum(1 for r in closed if r['entry'] and (r['peak'] / r['entry']) >= 10)
        zero = sum(1 for r in v if r <= -0.99)
        print(f'  均值 {sum(v)/len(v)*100:+.1f}% | 中位 {v[len(v)//2]*100:+.1f}%'
              f' | 峰值≥10x {ge10} 笔 | 归零(≤-99%) {zero} 笔')
    print(f'  距判据 150 笔还差 {max(0, 150-len(closed))} 笔已平仓')
    db.close()


if __name__ == '__main__':
    import sys
    if '--report' in sys.argv:
        report()
    else:
        print('实验臂请通过 paper_trade.py --ab 调用（同进程取价）')
