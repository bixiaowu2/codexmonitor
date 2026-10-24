#!/usr/bin/env python3
"""X 帖子配图生成(根据雷达数据)。

深色背景 + 中文字体(文泉驿正黑), 三类图:
  chart_scan():   今日 Alpha 妖币观察池 TOP3 集中度条形图
  chart_review(): 复盘 S 级持仓/集中度横向对比
  chart_signal(): 信号触发币的近7日价格走势
  chart_meme():   Meme 观察池聪明钱持仓与集中度
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
BG = '#0B0E14'
FG = '#F5F5F2'
MUTED = '#8A8A84'
GRID = '#1A1F2B'
GOLD = '#F0B90B'   # 币安金
RED = '#F87171'
GREEN = '#34D399'
CYAN = '#22D3EE'
BLUE = '#58A6FF'


def _style(ax):
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, labelsize=11)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.grid(axis='x', color=GRID, linewidth=0.6)


# 账号名打码（2026-10-24 风格令规范 5）。默认值 = 老行为，广场线不受影响。
DEFAULT_WATERMARK = '@bidafa1801919'
# 只留首字符 —— 保留「这是个账号」的观感，但尾巴数字不漏（留 a1919 等于没打码）。
MASKED_WATERMARK = '@b***'


def _save(fig, name, watermark=None):
    CHARTS.mkdir(parents=True, exist_ok=True)
    fig.text(0.995, 0.005, watermark or DEFAULT_WATERMARK, ha='right', va='bottom',
             color=MUTED, fontsize=9, alpha=0.85)
    path = CHARTS / name
    fig.savefig(path, dpi=150, facecolor=BG, bbox_inches='tight')
    plt.close(fig)
    return path


def chart_scan(name='scan.png', watermark=None):
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

    fig.text(0.01, 0.01, '三道筛：已上合约 · 未上现货 · 大户控盘≥60%',
             color=MUTED, fontsize=9.5)
    return _save(fig, name, watermark)


def chart_meme():
    """Meme 观察池: 聪明钱持仓人数 + 清洗后大户集中度 双面板(S=金色/A=蓝色)。"""
    try:
        rows = json.loads(Path('/var/lib/meme-radar/meme_matches.json').read_text())
    except Exception:
        return None
    sa = [r for r in rows if r.get('tier') in ('S', 'A')]
    sa.sort(key=lambda r: (0 if r.get('tier') == 'S' else 1, -(r.get('smart') or 0)))
    sa = sa[:8]
    if not sa:
        return None

    import numpy as np
    names = [str(r.get('symbol') or '?')[:14] for r in sa]
    smart = [int(r.get('smart') or 0) for r in sa]
    conc = [float((r.get('cleaned_top10') or 0) * 100) for r in sa]
    colors = [GOLD if r.get('tier') == 'S' else BLUE for r in sa]
    y = np.arange(len(names))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.6), facecolor=BG)
    fig.suptitle('Meme 观察池 · 聪明钱持仓与大户集中度', color=FG, fontsize=14, y=0.98)

    ax1.barh(y, smart, color=colors)
    ax1.set_yticks(y); ax1.set_yticklabels(names); ax1.invert_yaxis()
    ax1.set_title('聪明钱持仓人数', color=FG, fontsize=12)
    _style(ax1)
    for i, v in enumerate(smart):
        ax1.text(v, i, f' {v}', va='center', ha='left', color=FG, fontsize=10)
    ax1.set_xlim(0, (max(smart) or 1) * 1.2)

    ax2.barh(y, conc, color=colors)
    ax2.set_yticks(y); ax2.set_yticklabels([]); ax2.invert_yaxis()
    ax2.set_title('清洗后大户集中度 %', color=FG, fontsize=12)
    _style(ax2)
    for i, v in enumerate(conc):
        ax2.text(v, i, f' {v:.0f}%', va='center', ha='left', color=FG, fontsize=10)
    ax2.set_xlim(0, (max(conc) or 1) * 1.25)

    fig.text(0.01, 0.01, 'S=金色 / A=蓝色 · 集中度越低筹码越散',
             color=MUTED, fontsize=9.5)
    return _save(fig, 'meme.png')


def chart_smart_live(picks=None, top=6):
    """聪明钱扫货: 各币买入额横向条形(按链分色, 每条标注单位)。

    ⚠️ 数据里 SOL 与 BNB 是**不同计价单位**, 不能直接比大小 ——
    所以颜色区分链、标签写明单位, 让人一眼看出哪条是哪条。
    """
    if picks is None:
        try:
            import square_publish as _sp
            picks = _sp.smart_live_picks()
        except Exception:
            return None
    picks = (picks or [])[:top]
    if not picks:
        return None

    import numpy as np
    syms = [f"{x['sym'][:12]}" for x in picks]
    amts = [float(x['amt']) for x in picks]
    units = [x['qsym'] for x in picks]
    chains = [x['cn'] for x in picks]
    mcs = [float(x['mc']) for x in picks]
    ns = [int(x.get('n') or 1) for x in picks]                       # 成交笔数
    # 去重钱包数：一个钱包连买 9 笔只算 1 个地址，图上两个数都给，口径不混淆
    nw = [int(x['wallets']) if x.get('wallets') is not None else None for x in picks]

    # 按链分色(同一链同色), 便于看出单位差异
    palette = {'SOL': BLUE, 'BSC': GOLD, 'ETH': '#a371f7', 'BASE': '#3fb950'}
    colors = [palette.get(c, FG) for c in chains]

    y = np.arange(len(syms))[::-1]
    fig, ax = plt.subplots(figsize=(9, 0.72 * len(syms) + 1.6), facecolor=BG)
    ax.barh(y, amts, color=colors, height=0.62)
    ax.set_yticks(y)
    ax.set_yticklabels(syms, color=FG, fontsize=12)
    ax.set_xlabel('买入额（各条单位见右侧标注）', color=FG, fontsize=11)
    ax.set_title('聪明钱刚扫货 · 钱包池最新买入', color=GOLD, fontsize=15, pad=14)
    _style(ax)
    ax.grid(axis='x', color=GRID, linewidth=0.6)
    ax.grid(axis='y', visible=False)

    for yi, amt, u, mc, n, w in zip(y, amts, units, mcs, ns, nw):
        _w_s = f"{w}地址/" if w is not None else ""
        ax.text(amt * 1.02, yi, f"{amt:.3g} {u} · {mc/1e6:.2f}M$ · {_w_s}{n}笔",
                va='center', color=FG, fontsize=10)
    ax.set_xlim(0, max(amts) * 1.55)

    # 单位不同不能直接比 —— 显式说明, 避免读者误读
    if len(set(units)) > 1:
        ax.text(0.99, -0.16, f"注: 混了 {'/'.join(sorted(set(units)))} 两种计价单位，长度不可直接互比",
                transform=ax.transAxes, ha='right', va='top', color='#8b949e', fontsize=9.5)

    return _save(fig, 'smart_live.png')


def chart_smart_money(picks=None, top=8):
    """聪明钱买入榜: 各币"触发后涨幅"横向条形(绿正红负)。

    ⚠️ 两组语义不同, 不能混着读:
      刚 = 信号刚出、涨幅还小(还在窗口里)
      已 = 信号出过之后涨了多少(历史战绩)
    所以标签里带"刚/已"前缀, 而不是只靠颜色区分。
    """
    if picks is None:
        try:
            import square_publish as _sp
            picks = _sp.smart_money_picks()
        except Exception:
            return None
    if not picks:
        return None

    rows = []
    for tag, group in (('刚', picks.get('fresh') or []), ('已', picks.get('done') or [])):
        for r in group:
            try:
                g = picks['gain'](r)
            except Exception:
                g = None
            if g is None:
                continue
            rows.append((tag, str(r.get('ticker') or '?'), float(g), int(picks['cnt'](r))))
    if not rows:
        return None
    rows.sort(key=lambda x: -x[2])
    rows = rows[:top]

    import numpy as np
    labels = [f"{tag} {sym[:10]}" for tag, sym, _, _ in rows]
    gains = [g for _, _, g, _ in rows]
    counts = [c for _, _, _, c in rows]
    colors = [GREEN if g >= 0 else RED for g in gains]

    y = np.arange(len(labels))[::-1]
    fig, ax = plt.subplots(figsize=(9, 0.68 * len(labels) + 1.8), facecolor=BG)
    ax.barh(y, gains, color=colors, height=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, color=FG, fontsize=12)
    ax.set_xlabel('触发后涨幅 %', color=FG, fontsize=11)
    ax.set_title('聪明钱买入信号 · 触发后涨幅', color=GOLD, fontsize=15, pad=14)
    _style(ax)
    ax.grid(axis='x', color=GRID, linewidth=0.6)
    ax.grid(axis='y', visible=False)
    ax.axvline(0, color='#8b949e', linewidth=1.1)      # 零线加粗, 正负才看得清

    # 横轴按**实际数据范围**取, 不做对称 —— 全是正数时对称会白白浪费一半画布
    lo, hi = min(gains + [0.0]), max(gains + [0.0])
    span = max(hi - lo, abs(hi), abs(lo)) or 1
    off = span * 0.02
    for yi, g, c in zip(y, gains, counts):
        ax.text(g + (off if g >= 0 else -off), yi, f"{g:+.0f}% · {c}人",
                va='center', ha='left' if g >= 0 else 'right', color=FG, fontsize=10)
    ax.set_xlim(lo - span * 0.22, hi + span * 0.30)

    ax.text(0.99, -0.17,
            '刚 = 信号刚出、涨幅还小（还在窗口里）　已 = 信号出过之后的涨幅（历史战绩）',
            transform=ax.transAxes, ha='right', va='top', color='#8b949e', fontsize=9.5)
    return _save(fig, 'smart_money.png')


def chart_review(name='review.png', watermark=None):
    """复盘: S级持仓 vs 集中度双指标。"""
    try:
        b = json.loads(Path('/var/lib/demon-radar/baseline.json').read_text())
    except Exception:
        return None
    # 当前持仓取 matches.json，不取会冻结的 oi_snap（见 oi_now 注释）。
    # 取不到当前值的币**直接不画** —— 画成 0 会看起来像"持仓清零"。
    import oi_now
    _now = oi_now.current_oi()
    names, now_oi, base_oi = [], [], []
    for m in b.get('matches', [])[:5]:
        sym = m['symbol']
        cur = _now.get(sym)
        base = m.get('oi_notional') or m.get('oi')   # 字段名中途改过, 兼容两种
        if not cur or not base:
            continue
        names.append(sym)
        now_oi.append(cur / 1e6)
        base_oi.append(base / 1e6)
    if not names:
        return None

    import numpy as np
    y = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(9, 4.6), facecolor=BG)
    ax.barh(y + 0.2, base_oi, height=0.38, color='#30363d', label='基线持仓')
    # ±1% 以内算持平 —— 把 -0.03% 涂成红色「撤退」会误导读者
    _chg = [(n / b - 1) * 100 for n, b in zip(now_oi, base_oi)]
    colors = [GREEN if c >= 1 else RED if c <= -1 else '#8b949e' for c in _chg]
    ax.barh(y - 0.2, now_oi, height=0.38, color=colors, label='当前持仓')
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.set_xlabel('合约持仓名义（百万 USDT）', color=FG, fontsize=11)
    ax.set_title('Alpha雷达日复盘 · 合约持仓变化', color=GOLD, fontsize=15, pad=14)
    _style(ax)
    # 柱子的颜色表示**涨跌方向**, 不是"当前持仓"这一种颜色 ——
    # 原来图例只写"当前持仓(红)", 而实际涨的是绿的, 图例与配色对不上会误导。
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color='#30363d', label='基线持仓'),
                       Patch(color=GREEN, label='当前持仓 ↑ 加仓'),
                       Patch(color=RED, label='当前持仓 ↓ 撤退'),
                       Patch(color='#8b949e', label='当前持仓 · 持平(±1%)')],
              facecolor=BG, edgecolor=GRID, labelcolor=FG, loc='lower right')
    fig.text(0.01, 0.01, '持仓上升 = 资金在布局　持仓下降 = 资金在撤退',
             color=MUTED, fontsize=9.5)
    return _save(fig, name, watermark)


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
    fig.text(0.01, 0.01, '启动 ≠ 上涨，只说明资金费率转负',
             color=MUTED, fontsize=9.5)
    return _save(fig, f'signal_{symbol}.png')


if __name__ == '__main__':
    for fn in (chart_scan, chart_review):
        p = fn()
        print(f'{fn.__name__}: {p}')
