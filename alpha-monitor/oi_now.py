#!/usr/bin/env python3
"""复盘用的「当前持仓」—— 单一来源，避免三处各写一遍再各错一遍。

为什么不能用 track_state.json 的 oi_snap：

  demon_track 每 5 分钟跑一次，但只遍历 **S 档**：
      s_tier = [m for m in matches if m.get('tier') == 'S']
  所以一个币从 S 档掉到 A 档后，它在 oi_snap 里的值就**冻结**在掉档那一刻，
  再也不会更新。而复盘对 baseline.json 里的 S/A 档都算变动 —— 拿这个冻结值
  当「现在」，就会算出根本不存在的暴涨暴跌。

  实测 2026-10-20：
    AGT   matches.json 43.8M（与币安接口一致）  oi_snap 5.0M  → 复盘帖写「AGT -85%」
    龙虾  matches.json  8.9M                    oi_snap 17.0M → 复盘帖写「龙虾 +107%」
  两个都是假的，已经发到 X 和币安广场。

matches.json 由 demon-scan 每小时刷新，覆盖 S/A 全档，才是「现在」。
"""
import json
from pathlib import Path

D = Path('/var/lib/demon-radar')


def _oi(m):
    """baseline.json 的字段名中途从 oi 改成 oi_notional，两种都认。"""
    v = m.get('oi_notional') or m.get('oi')
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def current_oi():
    """当前全档持仓 {symbol: 名义值 USDT}，来自 demon-scan 刷新的 matches.json。"""
    try:
        ms = json.loads((D / 'matches.json').read_text(encoding='utf-8'))
    except Exception:
        return {}
    return {m['symbol']: _oi(m) for m in (ms or []) if m.get('symbol')}


def review_moves(baseline_matches, limit=5, thresh=5.0):
    """(ups, downs)，元素为 (symbol, chg%, base_百万USDT, cur_百万USDT, address)。

    当前值取不到的币**跳过**，不退回基线值 —— 退回会让 chg=0%，
    等于把「没数据」伪装成「没变化」。
    """
    now = current_oi()
    ups, downs = [], []
    for m in (baseline_matches or [])[:limit]:
        sym = m.get('symbol')
        if not sym:
            continue
        base = _oi(m)
        cur = now.get(sym)
        if base <= 0 or not cur:
            continue
        chg = (cur / base - 1) * 100
        row = (sym, chg, base / 1e6, cur / 1e6, m.get('address'))
        if chg >= thresh:
            ups.append(row)
        elif chg <= -thresh:
            downs.append(row)
    return ups, downs
