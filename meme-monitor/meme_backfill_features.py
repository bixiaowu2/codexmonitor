"""回填 meme 纸面交易的入场特征 —— 数据源是 meme-radar.sqlite 的 signals 表。

【为什么】
  paper_trade 只在开仓时从 meme_matches.json 取特征, 而 matches 里**没有动量字段**
  (change_5m/1h/6h/24h)。2026-10-24 实测: 入场动量是迄今唯一对「摸过 2x」有预测力的特征
  (change_6h AUC=0.757, change_24h AUC=0.738, change_1h AUC=0.706; 现有 score 只 0.583)。
  所以要把动量落库, 且**持续**落库, 才能前瞻复核。

【口径】取「开仓时刻(含 120s 容差)之前的最近一条信号」, 杜绝前视污染。
【幂等】可反复跑; 只对能匹配到信号的 position 写值。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict

RADAR = "/var/lib/meme-radar/meme-radar.sqlite"
PAPER = "/var/lib/meme-radar/paper_trade.sqlite"

# signal payload 字段 -> positions 列名
SIG2COL = {
    "change_5m": "change_5m", "change_1h": "change_1h",
    "change_6h": "change_6h", "change_24h": "change_24h",
    "volume_1h": "volume_1h", "buys_1h": "buys_1h", "sells_1h": "sells_1h",
    "liquidity_usd": "liquidity_usd", "market_cap": "market_cap",
    "smart_wallet_hits": "smart", "kol_hits": "kol_hits", "x_hits": "x_hits",
    "x_account_count": "x_account_count",
}
ALLCOLS = list(SIG2COL.values()) + ["score"]


def ensure_cols(db, table):
    cur = {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}
    for c in ALLCOLS:
        if c not in cur:
            db.execute("ALTER TABLE %s ADD COLUMN %s REAL" % (table, c))


def main() -> int:
    pc = sqlite3.connect(PAPER, timeout=20)
    pc.row_factory = sqlite3.Row
    ensure_cols(pc, "positions")
    ensure_cols(pc, "positions_history")

    targets = []
    for t in ("positions", "positions_history"):
        for r in pc.execute("SELECT rowid,key,chain,address,opened FROM %s" % t):
            targets.append((t, r["rowid"], (r["chain"], str(r["address"]).lower()), r["opened"] or 0))
    want = {k for _, _, k, _ in targets}
    if not want:
        print("无 position 需要回填")
        pc.close()
        return 0

    rc = sqlite3.connect("file:%s?mode=ro" % RADAR, uri=True, timeout=20)
    rc.row_factory = sqlite3.Row
    byaddr = defaultdict(list)
    for r in rc.execute("SELECT created,payload FROM signals"):
        try:
            pl = json.loads(r["payload"])
        except Exception:
            continue
        k = (pl.get("chain"), str(pl.get("address", "")).lower())
        if k not in want:
            continue
        f = {c: pl.get(s) for s, c in SIG2COL.items()}
        sm = pl.get("score_meta")
        f["score"] = sm.get("score") if isinstance(sm, dict) else None
        byaddr[k].append((r["created"], f))
    rc.close()

    filled = miss = 0
    for table, rowid, k, opened in targets:
        lst = [t for t in byaddr.get(k, []) if t[0] <= opened + 120]
        if not lst:
            miss += 1
            continue
        f = max(lst, key=lambda t: t[0])[1]
        sets = ", ".join("%s=?" % c for c in ALLCOLS)
        pc.execute("UPDATE %s SET %s WHERE rowid=?" % (table, sets),
                   tuple(f.get(c) for c in ALLCOLS) + (rowid,))
        filled += 1
    pc.commit()
    n = pc.execute("SELECT COUNT(*) FROM positions WHERE change_1h IS NOT NULL").fetchone()[0]
    tot = pc.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    pc.close()
    print("回填 %d 笔, 无匹配 %d 笔 | positions 有 change_1h 的: %d/%d" % (filled, miss, n, tot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
