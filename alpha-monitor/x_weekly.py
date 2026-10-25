#!/usr/bin/env python3
"""X 原创帖 · 七天版式表（总指挥 2026-10-25 派单，用户已批准）。

派单原文见 chat/to-claude.md 2026-10-25 两条（SIG 86d91bf4 / bdeebae2）：
  自 2026-10-26（周一）起执行，试行至 2026-11-08。**此表取代**此前「体式轮换」改进令的笼统口径。
  周一 榜单版 / 周二 验真版 / 周三 实测战报版 / 周四 清单工具版 /
  周五 数据图版 / 周六 复盘版 / 周日 互动前瞻版。

执行细则（原文）：
  1 每天供稿 1 条（按当日版式）；数据取供稿时最新台账/雷达，无新鲜数据时换常青版式
    （周四清单体/周日互动体）**或在稿头注明「今日无稿」及原因，绝不编数字**。
  2 字数沿用实测体：加权 ≤280、四行结构；周二/周四允许「主帖 + 1–2 条自回复」小串。
  3 不带 #币安神算子、不蹭广场活动。
  4 当天有重大裁定可临时换版式，稿头注明换版原因。
  5 配图由总指挥按定版风格制作；**供稿时在稿头注明当日版式，并列出建议入卡的 3 个关键数字**。
  6 KOL 回帖稿流不变。

【本模块的边界】
  - 只读数据文件（demon-radar / meme-radar / 图表目录），**不写任何台账、不发帖、不推 Telegram**。
  - 所有数字直接取自数据文件原文，绝不改写；取不到就返回「今日无稿」+原因，不编。
  - 版式与配图布局的对应见 `LAYOUT_OF`；渲染在 `x_card.py`。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import x_card

CST = timezone(timedelta(hours=8))

DEMON = Path('/var/lib/demon-radar')
MEME = Path('/var/lib/meme-radar')
PAPER = MEME / 'paper_trade.sqlite'
CHARTS = Path('/var/lib/x-publisher/charts')
# 状态文件路径可用环境变量覆盖 —— 只为离线实测用（生产不设此变量，同 x_draft_style 的做法）。
STATE = Path(os.environ.get('X_WEEKLY_STATE')
             or '/var/lib/alpha-radar/x_weekly_state.json')

# 星期（datetime.weekday(): 周一=0）→ (中文星期, 版式名, 配图布局, 是否允许自回复小串)
WEEKLY = {
    0: ('周一', '榜单版', 'rank', False),
    1: ('周二', '验真版', 'verify', True),
    2: ('周三', '实测战报版', 'report', False),
    3: ('周四', '清单工具版', 'steps', True),
    4: ('周五', '数据图版', 'original', False),
    5: ('周六', '复盘版', 'compare', False),
    6: ('周日', '互动前瞻版', 'ask', False),
}

# 生效日 —— 派单原文「自 2026-10-26（周一）起执行，试行至 2026-11-08」。
# 生效日之前（即 10-25 周日那几轮）仍按 10-24 风格改进令的两列草稿出，
# 保证「首稿为 2026-10-26 周一榜单版」与原文逐字对上。
EFFECTIVE_FROM = '2026-10-26'

X_LIMIT = 280
TARGET_LO, TARGET_HI = 170, 240


def _xlen(t):
    import sys
    sys.path.insert(0, '/opt/alpha-radar')
    import post_style
    return post_style.xlen(t)


def _snap(path):
    """快照口径 —— 取数据文件自己的 mtime（沿用 10-24 风格令规范 3 的口径）。"""
    try:
        m = Path(path).stat().st_mtime
    except Exception:
        return '快照时间未知'
    return '快照 ' + datetime.fromtimestamp(m, timezone.utc).strftime('%m-%d %H:%M UTC')


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def _ro_sqlite(path):
    """只读打开 —— 绝不以读写方式碰生产库。"""
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


# ---------------------------------------------------------------- 内容构建
def build_rank():
    """周一 榜单版：上周雷达汇总榜（费率 / 涨跌 Top 3–5），编号清单体。

    数据：demon-radar baseline.json（fr 资金费率 / chg24h 24h 涨跌）+ matches.json（控盘）。
    """
    b = _read_json(DEMON / 'baseline.json')
    if not b or not b.get('matches'):
        return None, 'baseline.json 无数据'
    m = _read_json(DEMON / 'matches.json') or []
    conc = {x['symbol']: x.get('cleaned_top10') for x in m if isinstance(x, dict)}
    rows = b['matches']
    by_chg = sorted(rows, key=lambda r: -(r.get('chg24h') or 0))
    by_fr = sorted(rows, key=lambda r: -(r.get('fr') or 0))
    top = by_chg[:3]
    lines = [f"{i}. {r['symbol']} 24h {r['chg24h']:+.2f}% · 费率 {r['fr']*100:.4f}%"
             for i, r in enumerate(top, 1)]
    hot = by_fr[0]
    head = f"Muse 把上周雷达榜过了一遍\n涨跌前三里 {top[0]['symbol']} 领跑"
    mech = '我用的口径：24h 涨跌 · 费率 · 控盘，取雷达原值'
    judge = (f"费率最高 {hot['symbol']} {hot['fr']*100:.4f}%\n"
             '不构成建议。你更看涨跌还是费率? 👇')
    text = '\n\n'.join([head, mech, '\n'.join(lines), judge + '\n' + _snap(DEMON / 'baseline.json')])
    keys = [
        f"{top[0]['symbol']} 24h {top[0]['chg24h']:+.2f}%",
        f"{hot['symbol']} 费率 {hot['fr']*100:.4f}%",
        f"入榜 {len(rows)} 个标的",
    ]
    rows_card = [(r['symbol'], f"{r['chg24h']:+.2f}%",
                  f"费率 {r['fr']*100:.4f}% · 控盘 {(conc.get(r['symbol']) or 0)*100:.0f}%")
                 for r in top]
    return {'main': text, 'keys': keys, 'card': ('rank', rows_card)}, None


def build_report():
    """周三 实测战报版：舰队/predict 真实台账，Muse 实测体，亏损数字不藏。

    数据：meme-radar paper_trade.sqlite（positions.realized / fills.kind），只读。
    """
    if not PAPER.exists():
        return None, 'paper_trade.sqlite 不存在'
    try:
        c = _ro_sqlite(PAPER)
        closed, total = c.execute(
            'select count(*), round(sum(realized), 4) from positions where closed is not null'
        ).fetchone()
        wins = c.execute(
            'select count(*) from positions where closed is not null and realized > 0'
        ).fetchone()[0]
        kinds = dict(c.execute('select kind, count(*) from fills group by kind').fetchall())
        worst = c.execute(
            'select symbol, round(realized,4) from positions where closed is not null '
            'order by realized asc limit 1').fetchone()
        c.close()
    except Exception as e:
        return None, f'读 paper_trade 失败: {type(e).__name__}'
    if not closed:
        return None, 'paper_trade 无平仓记录'
    hit = wins / closed * 100
    head = f"Muse 把纸面实测台账翻了一遍\n{closed} 笔平仓，累计 {total:+.2f} 单位"
    mech = '我的口径：移动止盈/止损自动平仓\n全部纸面模拟，未用真金'
    data = (f"正收益 {wins} 笔（{hit:.1f}%）· 负收益 {closed - wins} 笔\n"
            f"最差一笔 {worst[0].strip()} {worst[1]:+.4f}")
    judge = ('这套参数目前是负的，我不藏。\n'
             '出场规则按 10-24 裁决未动。\n先动参数还是先动选币? 👇')
    text = '\n\n'.join([head, mech, data, judge + '\n' + _snap(PAPER)])
    keys = [f"平仓 {closed} 笔", f"正收益 {wins} 笔（{hit:.1f}%）", f"累计 {total:+.2f} 单位"]
    card = ('report',
            [(f'{closed}', '平仓笔数', x_card.ACCENT),
             (f'{hit:.1f}%', '正收益率', x_card.WARN),
             (f'{total:+.2f}', '累计收益(单位)', x_card.WARN)],
            [(f'正收益 {wins} 笔', '含 tp1/tp2/trail', x_card.POS),
             (f'负收益 {closed - wins} 笔', f"含 stop {kinds.get('stop', 0) + kinds.get('stop_fast', 0)} 笔", x_card.WARN),
             (f"最差 {worst[0].strip()}", f'{worst[1]:+.4f}', x_card.WARN)])
    return {'main': text, 'keys': keys, 'card': card}, None


def build_steps():
    """周四 清单工具版：方法沉淀（常青，不依赖当日数据）。

    内容是**我方实查过**的三道筛口径 —— 取自 x_draft_style.content_pool 的同一套判据原文。
    """
    head = 'Muse 把筛妖币的三道筛抄了一遍\n过不了任意一条，我直接跳过'
    mech = '这套口径一共 3 条，主要是用来剔除的'
    steps = ['已上合约 —— 有真实资金在博弈',
             '未上现货 —— 现货上市前是布局窗口',
             '大户控盘 ≥60% —— 主力已经拿到筹码']
    data = '\n'.join(f'{i}. {s}' for i, s in enumerate(steps, 1))
    judge = '留下来的才有资格进榜。\n你觉得该不该再严一点?'
    text = '\n\n'.join([head, mech, data, judge])
    keys = ['3 条筛', '控盘门槛 60%', '已上合约 ∧ 未上现货']
    card = ('steps', [('已上合约', '有真实资金在博弈'),
                      ('未上现货', '现货上市前是布局窗口'),
                      ('大户控盘 ≥60%', '主力已经拿到筹码')])
    return {'main': text, 'keys': keys, 'card': card}, None


def build_data_chart():
    """周五 数据图版：当日一张雷达图 + 三行解读。

    原图必须是 **X 线自己出的打码图**（x_chart 传 MASKED_WATERMARK 写的 scan_x.png），
    不能拿广场线默认图 —— 那张水印是未打码账号名，违反打码令。
    """
    src = CHARTS / 'scan_x.png'
    b = _read_json(DEMON / 'baseline.json')
    if not b or not b.get('matches'):
        return None, 'baseline.json 无数据'
    if not src.exists():
        return None, 'scan_x.png 不存在（X 线打码图未生成）'
    rows = sorted(b['matches'], key=lambda r: -(r.get('chg24h') or 0))
    t = rows[0]
    head = f"Muse 今天只放一张图\n雷达原图，我没重绘"
    mech = '图是什么：Alpha 妖币观察池 TOP3 的大户集中度与合约持仓'
    data = (f"关键数字：{t['symbol']} 24h {t['chg24h']:+.2f}% · 费率 {t['fr']*100:.4f}%\n"
            f"入榜 {len(rows)} 个标的")
    judge = '一句判断：控盘高只说明筹码集中，不等于会涨。\n你看图里哪条更关键? 👇'
    text = '\n\n'.join([head, mech, data, judge + '\n' + _snap(DEMON / 'baseline.json')])
    keys = [f"{t['symbol']} 24h {t['chg24h']:+.2f}%", f"入榜 {len(rows)} 个", '雷达原图 1 张']
    card = ('original', str(src), 'Alpha 妖币观察池 TOP3 · 雷达原图', '数据筛选，不构成投资建议')
    return {'main': text, 'keys': keys, 'card': card}, None


def build_ask():
    """周日 互动前瞻版：一个带数据背景的真问题 + 下周观察清单 3 项，只列观察不预测。"""
    b = _read_json(DEMON / 'baseline.json')
    m = _read_json(DEMON / 'matches.json') or []
    if not b or not b.get('matches'):
        return None, 'baseline.json 无数据'
    n = len(b['matches'])
    conc = [x.get('cleaned_top10') or 0 for x in m if isinstance(x, dict)]
    hi = max(conc) * 100 if conc else 0
    head = 'Muse 想先问一句\n控盘高到底该不该跟?'
    mech = f'我的观察池现在 {n} 个标的，最高大户控盘 {hi:.0f}%'
    data = ('下周只看三件事：\n'
            '1. 这些标的的资金费率是否还在正区间\n'
            '2. 控盘 ≥60% 的那几个有没有上现货\n'
            '3. 纸面台账的移动止盈有没有把亏损收窄')
    judge = '只列观察，不预测方向。\n你会先看哪一条? 👇'
    text = '\n\n'.join([head, mech, data, judge + '\n' + _snap(DEMON / 'matches.json')])
    keys = [f'观察池 {n} 个', f'最高控盘 {hi:.0f}%', '观察清单 3 项']
    card = ('ask', '控盘高该不该跟?',
            ['资金费率是否还在正区间', '控盘 ≥60% 的有没有上现货', '移动止盈有没有收窄亏损'])
    return {'main': text, 'keys': keys, 'card': card}, None


BUILDERS = {
    'rank': build_rank,
    'report': build_report,
    'steps': build_steps,
    'original': build_data_chart,
    'ask': build_ask,
}


def plan(now=None):
    """当日版式 —— 按 CST 星期取（投稿节奏与 CST 对齐）。"""
    now = now or datetime.now(CST)
    dow = now.weekday()
    wd, name, layout, thread_ok = WEEKLY[dow]
    return {'weekday': wd, 'name': name, 'layout': layout, 'thread_ok': thread_ok,
            'date': now.strftime('%Y-%m-%d')}


def build(now=None):
    """出当日一条稿。返回 (draft, None) 或 (None, 无稿原因)。

    周二（验真版）/ 周六（复盘版）在 ARM 上**没有数据源**（见回执），
    按细则 1 返回「今日无稿」+原因，绝不编数字。
    """
    p = plan(now)
    # 细则 1：无新鲜数据时**换常青版式**（周四清单体 / 周日互动体），
    # 或在稿头注明「今日无稿」及原因 —— 绝不编数字。
    # 周二（验真）与周六（复盘）在 ARM 上无数据源，故走常青兜底并注明换版原因（细则 4）。
    NO_SOURCE = {
        'verify': ('steps', '今日无验真数据（ARM 无打新/合约验真台账）→ 按细则 1 换常青版式：周四清单体'),
        'compare': ('ask', '今日无复盘数据（ARM 无「五平台流量」台账）→ 按细则 1 换常青版式：周日互动体'),
    }
    layout, name, note = p['layout'], p['name'], None
    if layout not in BUILDERS:
        if layout in NO_SOURCE:
            layout, note = NO_SOURCE[layout]
            # 换版后的版式名（常青版式 = 周四清单工具版 / 周日互动前瞻版）
            name = {'steps': '清单工具版', 'ask': '互动前瞻版'}[layout]
        else:
            return None, '该版式无构建器'
    draft, err = BUILDERS[layout]()
    if err:
        return None, err
    draft.update({'weekday': p['weekday'], 'format': name, 'layout': layout,
                  'thread_ok': p['thread_ok'], 'date': p['date'], 'note': note})
    draft['xlen'] = _xlen(draft['main'])
    return draft, None


def header(draft):
    """稿头 —— 细则 5 要求：注明当日版式 + 列出建议入卡的 3 个关键数字。

    细则 4：临时换版式时，稿头注明换版原因。
    """
    h = (f"[{draft['weekday']} {draft['format']} · {draft['date']}] "
         f"xlen={draft['xlen']} · 建议入卡数字：{' / '.join(draft['keys'])}")
    if draft.get('note'):
        h += f" · 换版原因：{draft['note']}"
    return h


def in_effect(now=None):
    """派单生效日判定（CST）—— 未到生效日则退回旧两列草稿。"""
    now = now or datetime.now(CST)
    return now.strftime('%Y-%m-%d') >= EFFECTIVE_FROM


def already_ran_today(now=None):
    """每天只供 1 条（细则 1）—— 当天已出过稿就不再出。"""
    p = plan(now)
    st = _read_json(STATE, {}) or {}
    return st.get('last_date') == p['date']


def note_ran(now=None):
    p = plan(now)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({'last_date': p['date'],
                                 'last_format': p['name'],
                                 'ts': time.time()}, ensure_ascii=False), encoding='utf-8')
