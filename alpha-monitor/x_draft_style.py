#!/usr/bin/env python3
"""X 草稿体式池 —— 总指挥 2026-10-24 02:06 风格改进令（用户亲批）的落地件。

只服务 X 草稿链（x-draft.timer → x_publisher.py --draft）。
**不碰 post_variants.py** —— 那个模块同时被 square_publish.py 用（广场帖），
在共享模块里加 X 专属规矩（点名 Muse、建议发布时间）会串到广场线上去。

六条规范（原文见 chat/to-claude.md 2026-10-24 02:06）：
  1 字数：加权 ≤280（中文=2），目标 170–240；四行内：结论+主角 / 一行机制 /
    一行实测数字 / 一句判断
  2 主角点名 Muse（不写泛泛「AI」）；基础设施卖点可一句带过（永久免费甲骨文云）
  3 数字必须来自雷达/台账实数，注明快照口径；不利数字不藏；
    禁空泛喊单词、禁收益承诺
  4 体式轮换：实测日志体 / 公式反推体 / 清单体 / 对比体 / 问答体；
    同批 2 条不得同体式，连续两批不得重复同一开头句式
  5 配图：深色数据卡，大数字 + 机制三行；账号名 / Agent ID / IP 一律打码
  6 时段：草稿照常产出，但标注建议发布时间（晚高峰 20:00–22:00 CST）
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

# 状态文件路径可用环境变量覆盖 —— 只为离线实测用（生产不设此变量）。
STATE = Path(os.environ.get('X_DRAFT_STYLE_STATE')
             or '/var/lib/alpha-radar/x_draft_style_state.json')

X_LIMIT = 280
TARGET_LO, TARGET_HI = 170, 240

# 体式编号 → 名字（回执与日志里用中文名，方便对照）
STYLES = {
    1: '实测日志体',
    2: '公式反推体',
    3: '清单体',
    4: '对比体',
    5: '问答体',
}

# 建议发布时间（规范 6）
PUBLISH_HINT = '建议发布 20:00–22:00 CST'

# 基础设施卖点 —— 「可一句带过」，所以只在两个体式里出现，不是每条都挂
INFRA = '跑在永久免费的甲骨文云上'


def _load():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save(d):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


# ---------------------------------------------------------------- 批次状态
# 「同批 2 条不得同体式」—— 一批 = draft_today() 里的两条（观察池 + 复盘）。
# 「连续两批不得重复同一开头句式」—— 记上一批各条的 head 首行。

_BATCH_USED = set()


def begin_batch():
    """draft_today() 开头调一次，重置本批已用体式。"""
    global _BATCH_USED
    _BATCH_USED = set()


def pick(column, candidates):
    """挑体式：先排除本批已用的，再排除与上一批开头句式重复的。"""
    st = _load()
    pool = [c for c in candidates if c not in _BATCH_USED] or list(candidates)
    last_heads = st.get('last_heads') or {}
    pool2 = [c for c in pool if _head_key(c) not in last_heads.values()]
    if pool2:
        pool = pool2
    vid = random.choice(pool)
    _BATCH_USED.add(vid)
    st['last_pick'] = st.get('last_pick') or {}
    st['last_pick'][column] = vid
    _save(st)
    return vid


def _head_key(vid):
    """开头句式指纹 —— 用体式的骨架首句，不用整句（整句带币名，永远不重样）。"""
    return {1: 'Muse今天', 2: 'Muse反推', 3: 'Muse把池子',
            4: 'Muse把两个数', 5: 'Muse被问'}.get(vid, f'V{vid}')


def note_styles(vids):
    """draft_today() 末尾调一次，记下本批各条用的体式（存句式指纹，不存整句）。"""
    st = _load()
    st['last_heads'] = {str(i): _head_key(v) for i, v in enumerate(vids)}
    _save(st)


# ---------------------------------------------------------------- 组装
def _blocks(style, c):
    """按体式把内容排成 ≤4 段（规范 1 的「四行」）。返回 list[str]。"""
    d = c['data']
    snap = c['snap']

    if style == 1:      # 实测日志体 —— 从「我今天做了什么」起头
        head = f"Muse {c['action']}\n{c['conclusion']}"
        mech = c['mech']
        data = '\n'.join(d[:3])
        judge = f"{c['judge']}\n{snap} · {INFRA}"

    elif style == 2:    # 公式反推体 —— 把口径写成一条判定式
        head = f"Muse 反推了一遍{c['thing']}的口径\n{c['formula']}"
        mech = c['mech']
        data = '\n'.join(d[:2])
        judge = f"{c['judge']}\n{snap}"

    elif style == 3:    # 清单体 —— 逐条列，编号
        head = f"Muse 把池子过了一遍\n{c['conclusion']}"
        mech = c['mech']
        data = '\n'.join(f"{i}. {x}" for i, x in enumerate(d[:3], 1))
        judge = f"{c['judge']}\n{snap}"

    elif style == 4:    # 对比体 —— 两个数摆一起
        head = f"Muse 把两个数摆在一起\n{c['contrast_head']}"
        mech = c['mech']
        data = '\n'.join(d[:2])
        judge = f"{c['judge']}\n{snap}"

    else:               # 问答体 —— 用一句问句起头
        head = f"Muse 被问过同一个问题\n{c['question']}"
        mech = c['mech']
        data = '\n'.join(d[:3])
        judge = f"{c['judge']}\n{snap} · {INFRA}"

    return [b for b in (head, mech, data, judge) if b]

def render(style, c):
    """按体式出稿。返回 (text, meta)。"""
    blocks = _blocks(style, c)
    # 收尾：风格令要「一句判断」，post_style 规则 5 要「开放循环」。
    # 两者不冲突 —— 判断句后面挂一个短问句，仍在同一段（不破「四行」）。
    if c.get('loop') and '？' not in blocks[-1] and '?' not in blocks[-1]:
        blocks[-1] = blocks[-1] + '\n' + c['loop']
    text = '\n\n'.join(blocks)
    # 超限时先砍数据段（规范 1 宁可少列一个标的，也不能让开头被截断）
    if _xlen(text) > X_LIMIT and len(blocks) > 3:
        blocks[2] = '\n'.join(blocks[2].split('\n')[:1])
        text = '\n\n'.join(blocks)
    return text, {'style': style, 'style_name': STYLES[style],
                  'xlen': _xlen(text), 'blocks': len(blocks)}


def _xlen(t):
    """X 加权字数 —— 复用 post_style.xlen，口径只能有一处。"""
    import sys
    sys.path.insert(0, '/opt/alpha-radar')
    import post_style as ps
    return ps.xlen(t)


def content_pool(picks, snap):
    """妖币观察池的内容规格。picks: [{'symbol','conc','oi'}]，conc 百分数，oi 百万 USDT。"""
    syms = [(p['symbol'], p['conc'], p['oi']) for p in picks]
    n = len(syms)
    s0, c0, o0 = syms[0]
    hi, lo = syms[0], syms[-1]
    gap = hi[1] - lo[1]
    data = [f"{s} 控盘 {c:.0f}% · 持仓 {o:.1f}M USDT" for s, c, o in syms]
    if n == 1:
        data = [f"{s0} 控盘 {c0:.0f}% · 持仓 {o0:.1f}M USDT"]
    return {
        'thing': 'Alpha 妖币',
        'action': '今天重筛了一遍 Alpha 池',
        'conclusion': f"{n} 个过了三道筛",
        'formula': '已上合约 ∧ 未上现货 ∧ 大户控盘 ≥60%',
        'mech': '三道筛缺一条就不进池',
        'contrast_head': f"控盘差 {gap:.0f} 个百分点\n{hi[0]} {hi[1]:.0f}% vs {lo[0]} {lo[1]:.0f}%",
        'question': '控盘 80% 以上就该跟吗?',
        'data': data,
        'judge': '控盘高只是筹码集中, 不等于会涨\n我不下这个结论。',
        'loop': '我盯池子先看这条, 你会先看哪一条? 👇',
        'snap': snap,
    }


def content_review(ups, downs, snap):
    """每日复盘的内容规格。ups/downs: [(symbol, chg, b0, c0)]，chg 百分数，b0/c0 百万 USDT。"""
    nu, nd = len(ups), len(downs)
    rows = [f"🔺 {s} {c:+.0f}% · {b:.1f}M → {e:.1f}M" for s, c, b, e in ups[:3]] + \
           [f"🔻 {s} {c:+.0f}% · {b:.1f}M → {e:.1f}M" for s, c, b, e in downs[:3]]
    if not rows:
        rows = ['今天 0 个标的持仓变动超过 ±5%']
    total = nu + nd
    if nu and nd:
        concl = f"{nu} 个进 · {nd} 个出"
        chead = f"进 {nu} 个 vs 出 {nd} 个\n一边在买, 一边在走"
        question = '持仓异动到底算不算信号?'
    elif nu:
        concl = f"{nu} 个被加仓"
        chead = f"{nu} 个加仓 vs 0 个撤退\n单边"
        question = '只有加仓、没有撤退, 算信号吗?'
    elif nd:
        concl = f"{nd} 个被撤退"
        chead = f"0 个加仓 vs {nd} 个撤退\n单边"
        question = '集体撤退该不该跟?'
    else:
        concl = '持仓整体平稳'
        chead = '0 个加仓 vs 0 个撤退\n没有异动'
        question = '没有异动的那天, 还记不记?'
    return {
        'thing': '每日复盘',
        'action': '今天复盘了一遍持仓',
        'conclusion': concl,
        'formula': '|持仓变动| ≥5% 才进榜',
        'mech': '门槛 5%, 低于这个数不进榜',
        'contrast_head': chead,
        'question': question,
        'data': rows,
        'judge': f"共 {total} 个有动静\n单日变动说明不了什么, 我看连续几天。",
        'loop': '我只看方向, 你更在意单日还是趋势? 👇',
        'snap': snap,
    }
