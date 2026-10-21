"""纸面交易 —— 用真实信号 + 真实价格模拟成交，验证策略是否真的能盈利。

【为什么需要】
用户说"CEX 可以开交易权限，但前提是策略靠谱、我能盈利"。
在接真钱之前，必须先用**纸面交易**证明：按现有信号 + 现有止盈止损规则，实际是赚是亏。

【为什么不用回测】
账本(`forward_tracks`)只在 4 个检查点取价，粒度太粗，没法还原"先到 2x 还是先到止损"。
K线回填能补历史，但**样本池与实时信号不同**、且有前视偏差风险。
**纸面交易是前瞻的、无偏的** —— 从今天起记录每个 S 级信号假如下单会怎样。

【交易规则】（与 meme_track 一致）
  入场  = 信号出现当轮的 price_usd
  止损  = -50%（meme 波动大，-15% 会被反复扫）
  TP1   = 2x  → 卖出 1/3
  TP2   = 5x  → 再卖 1/3
  剩余 1/3: 从峰值回撤 30% 卖出；或持有满 7 天按现价结算

【不计入】滑点、手续费、MEV。真实成交会略差于此 —— 所以纸面**盈利**是必要条件，不是充分条件。

用法:
    paper_trade.py            # 跑一轮(记录新信号 + 检查持仓 + 结算)
    paper_trade.py --report   # 看统计
"""
from __future__ import annotations

import json, sqlite3, time
from pathlib import Path

DATA = Path('/var/lib/meme-radar')
DB = DATA / 'paper_trade.sqlite'
MATCHES = DATA / 'meme_matches.json'

STOP_PCT = 0.50          # 止损 -50%
TP1, TP2 = 2.0, 5.0      # 2x / 5x
TRAIL_PCT = 0.30         # 剩余仓位从峰值回撤 30%
MAX_HOLD_DAYS = 7
MIN_LIQ = 30000          # 流动性太薄的币不模拟(滑点会吃掉一切)


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS positions(
        key TEXT PRIMARY KEY, symbol TEXT, chain TEXT, address TEXT,
        entry REAL, opened REAL, peak REAL, stop REAL,
        tp1_done INT DEFAULT 0, tp2_done INT DEFAULT 0, closed INT DEFAULT 0,
        realized REAL DEFAULT 0, last_price REAL, last_at REAL, note TEXT,
        tier TEXT DEFAULT 'S')''')
    _cols = {r[1] for r in db.execute('PRAGMA table_info(positions)')}
    if 'tier' not in _cols:                      # 老库平滑加列
        db.execute("ALTER TABLE positions ADD COLUMN tier TEXT DEFAULT 'S'")
    db.execute('''CREATE TABLE IF NOT EXISTS fills(
        key TEXT, at REAL, kind TEXT, price REAL, mult REAL, pnl REAL)''')
    db.execute('CREATE INDEX IF NOT EXISTS fills_by_key ON fills(key, at)')


def price_of(c, matches):
    """优先用最新扫描里的 price_usd；掉出候选就用 DexScreener 兜底。"""
    row = matches.get((c.get('chain'), str(c.get('address', '')).lower()))
    if row and row.get('price_usd'):
        try:
            return float(row['price_usd'])
        except (TypeError, ValueError):
            pass
    try:
        import sources
        chain, addr = c.get('chain'), c.get('address')
        url = f'{sources.DEX}/tokens/v1/{chain}/{addr}'
        data = sources.get_json(url, 10)
        if isinstance(data, list) and data:
            best = max(data, key=lambda r: float((r.get('liquidity') or {}).get('usd') or 0))
            v = float(best.get('priceUsd') or 0)
            return v or None
    except Exception:
        pass
    return None


def run():
    now = time.time()
    try:
        rows = json.loads(MATCHES.read_text())
    except Exception:
        print('读不到 matches')
        return 2
    matches = {(x.get('chain'), str(x.get('address', '')).lower()): x for x in rows}

    db = sqlite3.connect(str(DB), timeout=15)
    db.row_factory = sqlite3.Row
    init(db)
    opened = closed = 0

    # ---- A. 新信号开仓(只做 S 级) ----
    for x in rows:
        if x.get('tier') not in ('S', 'A'):        # S 主组 + A 对照组
            continue
        if (x.get('liquidity_usd') or 0) < MIN_LIQ:
            continue
        key = f"{x.get('chain')}:{str(x.get('address', '')).lower()}"
        if db.execute('SELECT 1 FROM positions WHERE key=?', (key,)).fetchone():
            continue
        try:
            entry = float(x.get('price_usd') or 0)
        except (TypeError, ValueError):
            continue
        if entry <= 0:
            continue
        db.execute('INSERT INTO positions(key,symbol,chain,address,entry,opened,peak,stop,last_price,last_at,tier)'
                   ' VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (key, x.get('symbol'), x.get('chain'), x.get('address'),
                    entry, now, entry, entry * (1 - STOP_PCT), entry, now, x.get('tier')))
        opened += 1

    # ---- B. 检查未平仓 ----
    for p in db.execute('SELECT * FROM positions WHERE closed=0').fetchall():
        c = {'chain': p['chain'], 'address': p['address']}
        price = price_of(c, matches)
        if not price:
            continue
        entry, peak = p['entry'], max(p['peak'], price)
        db.execute('UPDATE positions SET peak=?, last_price=?, last_at=? WHERE key=?',
                   (peak, price, now, p['key']))
        mult = price / entry

        def close_part(kind, frac, reason):
            pnl = frac * (mult - 1)
            db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?)', (p['key'], now, kind, price, mult, pnl))
            db.execute('UPDATE positions SET realized=realized+? WHERE key=?', (pnl, p['key']))
            return pnl

        # 止损(整仓)
        if price <= p['stop']:
            close_part('stop', 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0, 'stop')
            db.execute("UPDATE positions SET closed=1, note=? WHERE key=?", ('止损', p['key']))
            closed += 1
            continue
        if not p['tp1_done'] and mult >= TP1:
            close_part('tp1', 1 / 3, 'tp1')
            db.execute('UPDATE positions SET tp1_done=1 WHERE key=?', (p['key'],))
        if not p['tp2_done'] and mult >= TP2:
            close_part('tp2', 1 / 3, 'tp2')
            db.execute('UPDATE positions SET tp2_done=1 WHERE key=?', (p['key'],))
        # 剩余仓位: 峰值回撤 30%
        if price <= peak * (1 - TRAIL_PCT):
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0:
                close_part('trail', left, 'trail')
                db.execute("UPDATE positions SET closed=1, note=? WHERE key=?", ('移动止盈', p['key']))
                closed += 1
                continue
        # 超期结算
        if now - p['opened'] >= MAX_HOLD_DAYS * 86400:
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0:
                close_part('timeout', left, 'timeout')
            db.execute("UPDATE positions SET closed=1, note=? WHERE key=?", ('持有到期', p['key']))
            closed += 1

    db.commit()
    db.close()
    print(f'纸面交易: 新开仓 {opened} | 平仓 {closed}')
    return 0


def report():
    db = sqlite3.connect(str(DB), timeout=15)
    db.row_factory = sqlite3.Row
    init(db)
    rows = db.execute('SELECT * FROM positions').fetchall()
    if not rows:
        print('还没有纸面交易记录 —— 等下一个 S 级信号')
        db.close()
        return
    closed = [r for r in rows if r['closed']]
    openp = [r for r in rows if not r['closed']]
    rets = [r['realized'] for r in closed]
    print('=' * 72)
    print(f'纸面交易统计: 共 {len(rows)} 笔 | 已平仓 {len(closed)} | 持仓中 {len(openp)}')
    print('=' * 72)
    from collections import defaultdict
    by_tier = defaultdict(list)
    for r in closed:
        by_tier[r['tier'] or 'S'].append(r['realized'])
    for t in ('S', 'A'):
        v = sorted(by_tier.get(t) or [])
        if not v:
            print(f'  [{t}] 尚无平仓样本')
            continue
        wins = sum(1 for x in v if x > 0)
        print(f'  [{t}级] n={len(v)} 中位 {v[len(v)//2]*100:+.1f}% | 平均 {sum(v)/len(v)*100:+.1f}% '
              f'| 胜率 {wins*100//len(v)}% | 最好 {v[-1]*100:+.0f}% | 最差 {v[0]*100:+.0f}%')
        print(f'        期望值 {sum(v)/len(v)*100:+.2f}%  ← 正的才值得接真钱')
    if openp:
        print(f'\n  持仓中(浮动):')
        for r in openp[:8]:
            cur = r['last_price'] or r['entry']
            print(f"    {str(r['symbol'])[:12]:<14} 入场 {r['entry']:.6g} 现价 {cur:.6g} "
                  f"浮动 {(cur/r['entry']-1)*100:+.0f}%")
    db.close()


if __name__ == '__main__':
    import sys
    if '--report' in sys.argv:
        report()
    else:
        raise SystemExit(run())
