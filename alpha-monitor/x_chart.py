#!/usr/bin/env python3
"""X 帖子配图生成(根据雷达数据)。

深色背景 + 中文字体(文泉驿正黑), 三类图:
  chart_scan():   今日 Alpha 妖币观察池 TOP3 集中度条形图
  chart_review(): 复盘 S 级持仓/集中度横向对比
  chart_signal(): 信号触发币的近7日价格走势
输出 PNG 到 /var/lib/x-publisher/charts/。
"""
from __future__ import annotations
import json, time
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

DATA = Path('/var/lib/x-publisher')
CHARTS = DATA / 'charts'

# 中文字体
for fp in ('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',):
    if Path(fp).exists():
        font_manager.fontManager.addfont(fp)
        plt.rcParams['font.family'] = 'WenQuanYi Zen Hei'
plt.rcParams['axes.unicode_minus'] = False

# 深色主题(X 加密审美)
BG = '#0d1117'
FG = '#e6edf3'
GRID = '#21262d'
GOLD = '#f0b90b'   # 币安金
RED = '#f85149'
GREEN = '#3fb950'
BLUE = '#58a6ff'


def _style(ax):
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, labelsize=11)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.grid(axis='x', color=GRID, linewidth=0.6)


def _save(fig, name):
    CHARTS.mkdir(parents=True, exist_ok=True)
    path = CHARTS / name
    fig.savefig(path, dpi=150, facecolor=BG, bbox_inches='tight')
    plt.close(fig)
    return path


def chart_scan():
    """观察池 TOP3: 集中度条形图(原始 vs 标注控盘)。"""
    try:
        m = json.loads(Path('/var/lib/demon-radar/matches.json').read_text())
    except Exception:
        return None
    sa = [x for x in m if x.get('tier') in ('S', 'A')][:3]
    if not sa:
        return None
    names = [x['symbol'] for x in sa]
    conc = [x['cleaned_top10'] * 100 for x in sa]
    oi = [x['oi_notional'] / 1e6 for x in sa]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2), facecolor=BG)
    fig.suptitle('Alpha妖币观察池 TOP3 · 大户控盘与合约持仓', color=FG, fontsize=14, y=0.98)

    bars = ax1.barh(names[::-1], conc[::-1], color=[GOLD, '#d29922', '#9e6a03'][::-1], height=0.55)
    ax1.set_xlim(0, 100)
    ax1.set_title('清洗后大户集中度 %', color=FG, fontsize=12)
    _style(ax1)
    ax1.grid(axis='x', color=GRID, linewidth=0.6)
    for b, v in zip(bars, conc[::-1]):
        ax1.text(v - 2, b.get_y() + b.get_height() / 2, f'{v:.0f}%',
                 va='center', ha='right', color='#0d1117', fontsize=12, fontweight='bold')

    bars2 = ax2.barh(names[::-1], oi[::-1], color=BLUE, height=0.55)
    ax2.set_title('合约持仓名义 (百万USDT)', color=FG, fontsize=12)
    _style(ax2)
    for b, v in zip(bars2, oi[::-1]):
        ax2.text(v, b.get_y() + b.get_height() / 2, f'{v:.1f}M',
                 va='center', ha='left', color=FG, fontsize=11)
    ax2.set_xlim(0, max(oi) * 1.25)

    return _save(fig, 'scan.png')


def chart_review():
    """复盘: S级持仓 vs 集中度双指标。"""
    try:
        b = json.loads(Path('/var/lib/demon-radar/baseline.json').read_text())
        t = json.loads(Path('/var/lib/demon-radar/track_state.json').read_text())
    except Exception:
        return None
    rows = b.get('matches', [])[:5]
    if not rows:
        return None
    names, now_oi, base_oi = [], [], []
    for m in rows:
        sym = m['symbol']
        cur = t.get('oi_snap', {}).get(sym)
        base = m.get('oi_notional')
        names.append(sym)
        now_oi.append((cur or 0) / 1e6)
        base_oi.append((base or 0) / 1e6)

    import numpy as np
    y = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(9, 4.6), facecolor=BG)
    ax.barh(y + 0.2, base_oi, height=0.38, color='#30363d', label='基线持仓')
    colors = [GREEN if n >= b else RED for n, b in zip(now_oi, base_oi)]
    ax.barh(y - 0.2, now_oi, height=0.38, color=colors, label='当前持仓')
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.set_title('Alpha雷达日复盘 · S级合约持仓变化', color=FG, fontsize=14)
    _style(ax)
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG)
    return _save(fig, 'review.png')


def chart_signal(symbol, futures_sym):
    """信号图: 近7日小时线收盘价。"""
    try:
        import urllib.request
        url = (f'https://fapi.binance.com/fapi/v1/klines?symbol={futures_sym}'
               f'&interval=1h&limit=168')
        req = urllib.request.Request(url, headers={'User-Agent': 'x-publisher/1'})
        raw = json.loads(urllib.request.urlopen(req, timeout=15).read())
        closes = [float(k[4]) for k in raw if isinstance(k, list) and len(k) >= 5]
        if len(closes) < 24:
            return None
    except Exception:
        return None
    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=BG)
    ax.plot(closes, color=GOLD, linewidth=1.8)
    ax.fill_between(range(len(closes)), closes, min(closes), color=GOLD, alpha=0.12)
    ax.set_title(f'{symbol} 近7日价格 (1h) · 启动信号触发', color=FG, fontsize=14)
    _style(ax)
    ax.grid(axis='y', color=GRID, linewidth=0.6)
    return _save(fig, f'signal_{symbol}.png')


if __name__ == '__main__':
    for fn in (chart_scan, chart_review):
        p = fn()
        print(f'{fn.__name__}: {p}')
