from __future__ import annotations

def score(row):
    change = float(row.get('change') or 0); vr = float(row.get('volume_ratio') or 0)
    # Theme is a research context, not a guarantee. Price and volume are capped to avoid chasing spikes.
    theme = float(row.get('theme_weight', 0)); momentum = max(0, min(change / 8, 1)) * 30
    volume = max(0, min(max(vr - 1, 0) / 3, 1)) * 25
    breakout = 15 if change >= 3 and vr >= 1.5 else 0
    overheated = 20 if change >= 12 else 0
    value = round(max(0, min(100, theme + momentum + volume + breakout - overheated)), 1)
    reasons = [row.get('theme', '主题观察')]
    if change > 0: reasons.append(f'近期涨幅{change:.1f}%')
    if vr >= 1.5: reasons.append(f'成交量约为基准{vr:.1f}倍')
    if breakout: reasons.append('价量同步突破')
    risk = ['仅基于公开行情，未验证财务、估值和事件真实性']
    if change >= 12: risk.append('短期涨幅过热')
    if vr >= 4: risk.append('成交量异常放大')
    return {'score': value, 'stage': 'hot' if value >= 70 else 'watch', 'reasons': reasons, 'risk': risk}
