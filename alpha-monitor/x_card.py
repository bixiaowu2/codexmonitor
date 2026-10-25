#!/usr/bin/env python3
"""X 配图 · 深色数据卡（总指挥 2026-10-25 配图风格规范，用户已确认）。

规范原文见 chat/to-claude.md 2026-10-25「补充派单：X 配图风格规范 + 按版式布局表」：
  深色数据卡 1200×628；色板 底 #0B111C / 面板 #121B2B / 主强调 #38BDF8 /
  警示 #FBBF24 / 正向 #34D399 / 正文 #EBF0F8 / 次要 #96A3B8；字体 Noto Sans CJK；
  账号名、Agent ID、IP、路径一律打码（•••式）或不出现。

按版式布局（同一派单）：
  周一 榜单卡 / 周二 验真要点卡 / 周三 战报卡 / 周四 步骤卡 /
  周五 原图版 / 周六 对比卡 / 周日 极简提问卡

**与广场线的边界**：本模块只服务 X 线。广场线仍走 x_chart 的默认水印与默认文件名，
产物必须逐字节不变（10-24 风格令已定：x_chart.DEFAULT_WATERMARK 保留不动）。
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Rectangle

# ---------------------------------------------------------------- 色板（原文）
BG = '#0B111C'
PANEL = '#121B2B'
ACCENT = '#38BDF8'
WARN = '#FBBF24'
POS = '#34D399'
FG = '#EBF0F8'
MUTED = '#96A3B8'

CARD_W, CARD_H, DPI = 1200, 628, 100

# 账号信息打码（•••式）—— 规范明写「账号名、Agent ID、IP、路径一律打码或不出现」
MASK = '@b•••'
WATERMARK = MASK

FONT_CANDIDATES = (
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
)

_font_ready = False


def _init_font():
    global _font_ready
    if _font_ready:
        return
    for fp in FONT_CANDIDATES:
        if Path(fp).exists():
            try:
                font_manager.fontManager.addfont(fp)
            except Exception:
                pass
    for name in ('Noto Sans CJK SC', 'Noto Sans CJK JP', 'Noto Sans CJK TC'):
        try:
            plt.rcParams['font.family'] = name
            break
        except Exception:
            continue
    plt.rcParams['axes.unicode_minus'] = False
    _font_ready = True


def _canvas():
    """固定 1200×628 画布 —— 不用 bbox_inches='tight'，否则尺寸会被裁掉。"""
    _init_font()
    fig = plt.figure(figsize=(CARD_W / DPI, CARD_H / DPI), dpi=DPI, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, CARD_W)
    ax.set_ylim(0, CARD_H)
    ax.axis('off')
    ax.set_facecolor(BG)
    return fig, ax


def _panel(ax, x, y, w, h, color=PANEL):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=14',
                                linewidth=0, facecolor=color, zorder=1))


def _title_bar(ax, title, sub=None):
    ax.add_patch(Rectangle((0, CARD_H - 96), CARD_W, 96, linewidth=0,
                           facecolor=PANEL, zorder=1))
    ax.add_patch(Rectangle((0, CARD_H - 96), 10, 96, linewidth=0,
                           facecolor=ACCENT, zorder=2))
    ax.text(44, CARD_H - 50, title, color=FG, fontsize=27, fontweight='bold',
            va='center', ha='left', zorder=3)
    if sub:
        ax.text(CARD_W - 44, CARD_H - 50, sub, color=MUTED, fontsize=15,
                va='center', ha='right', zorder=3)


def _foot(ax, note=''):
    ax.text(44, 26, note, color=MUTED, fontsize=13, va='center', ha='left', zorder=3)
    ax.text(CARD_W - 44, 26, WATERMARK, color=MUTED, fontsize=13,
            va='center', ha='right', zorder=3)


def _big(ax, x, y, value, label, color=ACCENT, size=52):
    ax.text(x, y, value, color=color, fontsize=size, fontweight='bold',
            va='center', ha='left', zorder=3)
    ax.text(x, y - 46, label, color=MUTED, fontsize=14, va='center', ha='left', zorder=3)


def _save(fig, out):
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return p


# ---------------------------------------------------------------- 七种版式
def card_rank(out, rows, title='上周雷达汇总榜', sub='Top 榜'):
    """周一 榜单卡：排名行 Top 3–5（名次 + 项目 + 关键数）。"""
    fig, ax = _canvas()
    _title_bar(ax, title, sub)
    y = CARD_H - 160
    for i, (name, value, note) in enumerate(rows[:5], 1):
        _panel(ax, 44, y - 62, CARD_W - 88, 74)
        ax.text(78, y - 25, f'{i}', color=ACCENT, fontsize=30, fontweight='bold',
                va='center', ha='left', zorder=3)
        ax.text(140, y - 25, str(name), color=FG, fontsize=24, fontweight='bold',
                va='center', ha='left', zorder=3)
        ax.text(CARD_W - 78, y - 25, str(value), color=WARN, fontsize=24,
                fontweight='bold', va='center', ha='right', zorder=3)
        if note:
            ax.text(CARD_W - 78, y - 50, str(note), color=MUTED, fontsize=13,
                    va='center', ha='right', zorder=3)
        y -= 88
    _foot(ax)
    return _save(fig, out)


def card_verify(out, items, conclusion, title='合约验真要点'):
    """周二 验真要点卡：检查项逐行（带 ✓/✗），底部一行结论。"""
    fig, ax = _canvas()
    _title_bar(ax, title, '逐项实查')
    y = CARD_H - 160
    for label, ok in items[:5]:
        # ✗(U+2717) 不在 Noto Sans CJK 里，实测会渲染成豆腐块 —— 用 × 代替
        mark, color = ('✓', POS) if ok else ('×', WARN)
        ax.text(80, y, mark, color=color, fontsize=30, fontweight='bold',
                va='center', ha='left', zorder=3)
        ax.text(130, y, str(label), color=FG, fontsize=22, va='center',
                ha='left', zorder=3)
        y -= 62
    _panel(ax, 44, 78, CARD_W - 88, 74)
    ax.text(78, 115, str(conclusion), color=WARN, fontsize=21, fontweight='bold',
            va='center', ha='left', zorder=3)
    _foot(ax)
    return _save(fig, out)


def card_report(out, bigs, rows, title='实测战报'):
    """周三 战报卡：大数字行 + 项目明细行。"""
    fig, ax = _canvas()
    _title_bar(ax, title, '台账实数')
    x = 60
    for value, label, color in bigs[:3]:
        _big(ax, x, CARD_H - 190, str(value), str(label), color=color)
        x += 380
    y = CARD_H - 300
    for name, value, color in rows[:4]:
        ax.text(64, y, str(name), color=FG, fontsize=19, va='center', ha='left', zorder=3)
        ax.text(CARD_W - 64, y, str(value), color=color, fontsize=19, fontweight='bold',
                va='center', ha='right', zorder=3)
        y -= 46
    _foot(ax)
    return _save(fig, out)


def card_steps(out, steps, title='方法清单'):
    """周四 步骤卡：编号步骤逐行（标题 + 一句话）。"""
    fig, ax = _canvas()
    _title_bar(ax, title, '常青')
    y = CARD_H - 158
    for i, (head, note) in enumerate(steps[:4], 1):
        ax.text(70, y, f'{i:02d}', color=ACCENT, fontsize=26, fontweight='bold',
                va='center', ha='left', zorder=3)
        ax.text(140, y + 12, str(head), color=FG, fontsize=21, fontweight='bold',
                va='center', ha='left', zorder=3)
        ax.text(140, y - 18, str(note), color=MUTED, fontsize=15,
                va='center', ha='left', zorder=3)
        y -= 96
    _foot(ax)
    return _save(fig, out)


def card_original(out, source_png, title, caption):
    """周五 原图版：雷达原图为主，只加统一深色边框与标题条，**不重绘数据**。

    源图必须是 X 线自己出的打码图（x_chart 传 MASKED_WATERMARK），
    不能直接拿广场线默认图 —— 那张水印是未打码账号名。
    """
    from PIL import Image
    src = Path(source_png)
    if not src.exists():
        return None
    _init_font()
    img = Image.open(src).convert('RGB')
    bar = 96
    inner_w, inner_h = CARD_W - 88, CARD_H - bar - 108
    scale = min(inner_w / img.width, inner_h / img.height)
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                     Image.LANCZOS)
    canvas = Image.new('RGB', (CARD_W, CARD_H), BG)
    canvas.paste(img, ((CARD_W - img.width) // 2, 60 + (inner_h - img.height) // 2))
    from PIL import ImageDraw, ImageFont
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, CARD_W, bar], fill=PANEL)
    d.rectangle([0, 0, 10, bar], fill=ACCENT)
    font_path = '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
    f_title = ImageFont.truetype(font_path, 34) if Path(font_path).exists() else None
    f_small = ImageFont.truetype(font_path, 19) if Path(font_path).exists() else None
    d.text((44, 30), title, fill=FG, font=f_title)
    d.text((44, CARD_H - 34), caption, fill=MUTED, font=f_small)
    d.text((CARD_W - 130, CARD_H - 34), WATERMARK, fill=MUTED, font=f_small)
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(p)
    return p


def card_compare(out, cols, title='本周 vs 上周'):
    """周六 对比卡：两列（本周 / 上周），涨跌用绿/黄标。

    cols = [(列名, [(项名, 值, delta 正负 or None), ...]), ...]，只取两列。
    """
    fig, ax = _canvas()
    _title_bar(ax, title, '逐项对照')
    for idx, (col, rows) in enumerate(list(cols)[:2]):
        x0 = 44 + idx * (CARD_W // 2 - 22)
        w = CARD_W // 2 - 66
        _panel(ax, x0, 96, w, CARD_H - 240)
        accent = ACCENT if idx == 0 else WARN
        ax.text(x0 + 30, CARD_H - 160, str(col), color=accent, fontsize=22,
                fontweight='bold', va='center', ha='left', zorder=3)
        y = CARD_H - 230
        for name, value, delta in list(rows)[:4]:
            color = MUTED if delta is None else (POS if delta >= 0 else WARN)
            ax.text(x0 + 30, y, str(name), color=FG, fontsize=17, va='center',
                    ha='left', zorder=3)
            ax.text(x0 + w - 30, y, str(value), color=color, fontsize=17,
                    fontweight='bold', va='center', ha='right', zorder=3)
            y -= 52
    _foot(ax)
    return _save(fig, out)


def card_ask(out, question, items, title='下周观察'):
    """周日 极简提问卡：大字问题 + 观察清单 3 项。"""
    fig, ax = _canvas()
    _title_bar(ax, title, '只列观察')
    ax.text(CARD_W // 2, CARD_H - 200, str(question), color=FG, fontsize=30,
            fontweight='bold', va='center', ha='center', zorder=3)
    y = CARD_H - 300
    for i, it in enumerate(items[:3], 1):
        ax.text(80, y, f'{i}.', color=ACCENT, fontsize=22, fontweight='bold',
                va='center', ha='left', zorder=3)
        ax.text(130, y, str(it), color=MUTED, fontsize=20, va='center',
                ha='left', zorder=3)
        y -= 62
    _foot(ax)
    return _save(fig, out)


LAYOUTS = {
    'rank': card_rank, 'verify': card_verify, 'report': card_report,
    'steps': card_steps, 'original': card_original, 'compare': card_compare,
    'ask': card_ask,
}
