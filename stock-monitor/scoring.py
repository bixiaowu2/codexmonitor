from __future__ import annotations

# ---------- 权重(可调) ----------
# 改前: theme + momentum30 + volume25 + breakout15 - overheated20
# 问题: 70 分是**当日价量**, 完全没有"上行空间"维度 → 排名只是"今天涨得多的"。
# 改后: 加入 upside(市值越小分越高), 并把价量权重下调, 免得当日涨跌主导。
# 注意副作用: 大盘蓝筹(无 cap_usd)会净掉约 10 分, 可能掉出 S 级 —— 这是有意为之(10x 靠小基数)。
W_MOMENTUM, W_VOLUME, W_BREAKOUT, W_OVERHEATED = 25, 15, 15, 20
W_UPSIDE = 20


def upside(cap_usd):
    """上行空间: 市值越小, 10x 所需的增量越小。无数据=0。"""
    try:
        c = float(cap_usd)
    except (TypeError, ValueError):
        return 0
    if c <= 0:
        return 0
    if c <= 3e8:
        return W_UPSIDE            # ≤3亿美元
    if c <= 1e9:
        return W_UPSIDE * 0.75     # ≤10亿
    if c <= 2e9:
        return W_UPSIDE * 0.50     # ≤20亿
    if c <= 5e9:
        return W_UPSIDE * 0.25     # ≤50亿
    return 0


def score(row):
    change = float(row.get('change') or 0); vr = float(row.get('volume_ratio') or 0)
    # Theme is a research context, not a guarantee. Price and volume are capped to avoid chasing spikes.
    theme = float(row.get('theme_weight', 0)); momentum = max(0, min(change / 8, 1)) * W_MOMENTUM
    volume = max(0, min(max(vr - 1, 0) / 3, 1)) * W_VOLUME
    breakout = W_BREAKOUT if change >= 3 and vr >= 1.5 else 0
    overheated = W_OVERHEATED if change >= 12 else 0
    ups = upside(row.get('cap_usd'))
    value = round(max(0, min(100, theme + ups + momentum + volume + breakout - overheated)), 1)
    reasons = [row.get('theme', '主题观察')]
    cap = row.get('cap_usd')
    if cap:
        try:
            reasons.append(f'市值{float(cap)/1e8:.1f}亿美元')
        except (TypeError, ValueError):
            pass
    if change > 0: reasons.append(f'较上交易日末根有效K线{change:.1f}%')
    if vr >= 1.5: reasons.append(f'成交量约为基准{vr:.1f}倍')
    if breakout: reasons.append('价量同步突破')
    risk = ['仅基于公开行情，未验证财务、估值和事件真实性']
    if change >= 12: risk.append('短期涨幅过热')
    if vr >= 4: risk.append('成交量异常放大')
    if not row.get('fresh', False): risk.append('休市或行情过期，不触发即时通知')
    if row.get('change') is None: risk.append('上交易日价格缺失')
    if row.get('volume_ratio') is None: risk.append('成交量基准不足')
    return {'score': value, 'stage': 'hot' if value >= 70 else 'watch', 'reasons': reasons, 'risk': risk}
