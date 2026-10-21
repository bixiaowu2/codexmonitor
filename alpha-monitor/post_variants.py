#!/usr/bin/env python3
"""帖子变体池 —— 让同一栏目的内容不再千篇一律。

问题：原来的模板结构固定（开头句式、结尾问句、解释段全一样），
     每次只换币名和数字 → 读者一眼看出是机器发的。

做法：
  1. 每个栏目准备 4-5 套**结构不同**的模板
  2. 按「当天数据特征」选（几个标的 / 差异大不大 / 有无异常）
  3. 加轮换：同一套不连续用两次

用法：
  from post_variants import pick
  vid = pick('alpha_pool', n=3, spread=0.30, has_anomaly=False)

覆盖栏目：
  alpha_pool    妖币观察池（Alpha）
  alpha_review  每日复盘（Alpha）
  meme_pool     Meme 观察池
  smart_live    聪明钱扫货
  smart_money   聪明钱买入榜
"""
import json
import random
import re
from pathlib import Path

STATE = Path('/var/lib/alpha-radar/variant_state.json')


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


# ---- 结尾 hashtag：每个变体一个，跟着变体轮换 ----
# 原来 5 个变体结尾 hashtag 完全一样 → 实测 12 条里 11 条结尾一致。
_TAGS = {
    'alpha': ['#BinanceAlpha', '#BinanceAlpha #Crypto', '#Alpha #Binance',
              '#Binance #Alpha', '#Alpha #Crypto'],
    'meme': ['#MemeCoin #Crypto', '#MemeCoin', '#Meme #Crypto',
             '#MemeCoin #Binance', '#Meme #Binance'],
}
_TAG_RE = re.compile(r'#\w+(?:\s+#\w+)*\s*$')


def _retag(tail, vid, family):
    """把结尾 hashtag 换成该变体专属的。tail 是帖子最后一段。"""
    if not tail:
        return tail
    tag = _TAGS[family][(vid - 1) % len(_TAGS[family])]
    return _TAG_RE.sub(tag, tail.rstrip())


def _rotate(column, allowed, limit=None):
    """从 allowed 里挑一个变体编号（列表靠前的更合适），排除上次用过的。

    limit: 只在前 N 个候选里挑（None = 全部）。
           给 pick() 用 3 —— 「合适」优先；栏目自己排好序的 allowed 传 None。
    """
    top = list(allowed)[:limit] if limit else list(allowed)
    if not top:
        return 1
    st = _load()
    last = st.get(column)
    pool = [c for c in top if c != last] or top
    vid = random.choice(pool)
    st[column] = vid
    _save(st)
    return vid


def pick(column, n=0, spread=0.0, has_anomaly=False):
    """按数据特征选变体编号（1 起）。

    column: 栏目名
    n: 标的个数
    spread: 标的间差异度（如控盘度最大-最小）
    has_anomaly: 有没有异常数据
    """
    # 1) 按特征排优先级（前面的更合适，但不是唯一选择）
    if n <= 1:
        cands = [2, 5]              # 聚焦型 / 观察型
    elif n == 2:
        cands = [3, 5, 2, 6]        # 对比 / 观察 / 聚焦 / 递进
    else:
        cands = [1, 3, 5, 6, 7]     # 清单 / 对比 / 观察 / 递进 / 失效条件

    if has_anomaly:
        cands = [4] + cands         # 有异常 → 悬念型插到最前
    if spread >= 0.20:
        cands = [3] + cands         # 差异大 → 对比型插到最前

    # 去重，保持优先级顺序
    seen = set()
    cands = [c for c in cands if not (c in seen or seen.add(c))]
    # 不截断 —— 候选本身就是「适配这个数据形状」的集合，截断只会砍掉多样性
    return _rotate(column, cands)


# ============================================================
# 妖币观察池（Alpha）
# ============================================================

def alpha_pool(vid, picks, n_smart=None, threads=None):
    """picks: [{'symbol','conc','oi'}]，conc 是百分数，oi 是百万 USDT。"""
    syms = [(p['symbol'], p['conc'], p['oi']) for p in picks]
    n = len(syms)

    if vid == 1:      # 清单型
        head = f"我扒了全部币安Alpha代币\n只有 {n} 个同时过三道筛"
        medals = '🥇🥈🥉'
        items = [f"{medals[i]} {s}  控盘 {c:.0f}%\n合约持仓 {o:.1f}M USDT"
                 for i, (s, c, o) in enumerate(syms)]
        tail = ("三道筛: 已上合约 · 未上现货 · 大户控盘≥60%\n"
                "现货上市前, 是资金布局的窗口\n\n"
                "这三个你先看哪个? 👇\n#BinanceAlpha")

    elif vid == 2:    # 聚焦型
        s, c, o = syms[0]
        head = "今天整个 Alpha 池\n只有这一个同时满足三条"
        items = [f"{s}  控盘 {c:.0f}%\n合约持仓 {o:.1f}M USDT"]
        tail = ("我盯的三道筛里最难的是「未上现货」\n"
                "—— 意味着还有布局窗口\n\n"
                "你盯过这类标的吗? 👇\n#BinanceAlpha")

    elif vid == 3:    # 对比型
        hi, lo = syms[0], syms[-1]
        gap = hi[1] - lo[1]
        head = f"今天这 {n} 个币\n控盘度差了 {gap:.0f} 个百分点"
        items = [f"{s}  控盘 {c:.0f}%" for s, c, _ in syms]
        tail = ("我盯的是集中度\n"
                "同样过了三道筛, 但筹码集中度完全不同\n\n"
                "集中度高不等于会涨, 只是筹码更稳\n\n"
                "你会先看哪个? 👇\n#BinanceAlpha")

    elif vid == 4:    # 悬念型
        s, c, o = syms[0]
        head = "有个 Alpha 币持仓在涨\n但我没放进今天的池子"
        items = [f"{s}  控盘 {c:.0f}%\n持仓 {o:.1f}M USDT"]
        tail = ("原因是它已经上现货了\n"
                "—— 布局窗口过了\n\n"
                "我的筛选里「未上现货」是硬条件\n\n"
                "你觉得这个条件该不该留? 👇\n#BinanceAlpha")

    elif vid == 5:    # 观察型
        head = f"今天 Alpha 池比较安静\n只有 {n} 个过筛"
        items = [f"{s}  控盘 {c:.0f}%" for s, c, _ in syms]
        tail = ("我盯的池子没有新面孔\n"
                "和前几天差不多\n\n"
                "这种时候我不发新东西也行\n\n"
                "你更想看哪种? 👇\n#BinanceAlpha")

    elif vid == 6:    # 递进型 —— 按确定性排序，并说明排序逻辑
        rows = [f"{i}. {s}  控盘 {c:.0f}%\n持仓 {o:.1f}M USDT"
                for i, (s, c, o) in enumerate(syms, 1)]
        head = "这三个不是一样的\n我按把握大小排了序"
        items = rows
        tail = ("排在前面的, 控盘更集中\n"
                "把握大不等于会涨, 只是筹码更稳\n\n"
                "我会先盯第一个\n\n"
                "你会怎么排? 👇\n#BinanceAlpha")

    else:             # 7 失效条件型 —— 诚实给「什么情况下逻辑不成立」
        items = [f"{s}  控盘 {c:.0f}%" for s, c, _ in syms]
        head = f"这三个同时过了筛\n但逻辑会失效"
        tail = ("两种情况我会把它们划掉:\n"
                "① 现货一上, 布局窗口就没了\n"
                "② 大户控盘掉到 60% 以下\n\n"
                "所以我每天重筛一遍\n\n"
                "你会设什么退出条件? 👇\n#BinanceAlpha")

    return head, items, _retag(tail, vid, 'alpha')


# ============================================================
# 每日复盘（Alpha）
# ============================================================

def alpha_review(ups, downs):
    """ups/downs: [(symbol, chg, b0, c0)] —— chg 是百分数，b0/c0 是百万 USDT。

    数据形状决定哪些变体成立（不能写「0 个撤退」这种自相矛盾的标题），
    再在成立的变体里轮换。返回 (vid, head, items, tail)。

    V6 是唯一的**故事型**：不从结论起头，从一个具体时刻起头。
    依据是 6 篇 X 拆解 —— 同样信息量，故事型 9,290 看 / 干货型 1,284 看。
    """
    nu, nd = len(ups), len(downs)

    if nu and nd:
        allowed = [1, 3, 2, 5, 6]  # 单边加仓 / 换仓 / 单边撤退 / 观察 / 自述
    elif nu:
        allowed = [1, 5, 6]        # 只有加仓（V3 需要两边都有，不能用）
    elif nd:
        allowed = [2, 5, 6]        # 只有撤退
    else:
        allowed = [4]              # 平稳（V6 要有异动才成立）

    vid = _rotate('alpha_review', allowed)

    if vid == 1:      # 单边加仓
        head = f"{nu} 个 Alpha 币被资金加仓\n钱在往哪走?"
        items = [f"🔺 {s}  {c:+.0f}%\n{b:.1f}M → {e:.1f}M USDT"
                 for s, c, b, e in ups[:3]]
        tail = ("持仓上升 = 资金在布局\n\n"
                "我盯的是变化, 不是绝对数\n\n"
                "你更关注加仓还是撤退? 👇\n#BinanceAlpha")

    elif vid == 2:    # 单边撤退
        head = f"{nd} 个 Alpha 币遭资金撤退\n谁在被抛弃?"
        items = [f"🔻 {s}  {c:+.0f}%\n{b:.1f}M → {e:.1f}M USDT"
                 for s, c, b, e in downs[:3]]
        tail = ("持仓下降 = 资金在撤退\n\n"
                "我盯的是持仓, 不是价格\n"
                "跌的时候更该看持仓\n\n"
                "你会跟着撤吗? 👇\n#BinanceAlpha")

    elif vid == 3:    # 换仓
        head = f"钱在换仓\n{nu} 个加仓 · {nd} 个撤退"
        items = [f"🔺 {s}  {c:+.0f}%" for s, c, _, _ in ups[:2]] + \
                [f"🔻 {s}  {c:+.0f}%" for s, c, _, _ in downs[:2]]
        tail = ("一边进一边出\n"
                "这是典型的换仓信号\n\n"
                "我一般先看进的那边\n\n"
                "你看到的是哪一种? 👇\n#BinanceAlpha")

    elif vid == 5:    # 观察型（任何形状都成立）
        rows = [f"🔺 {s}  {c:+.0f}%" for s, c, _, _ in ups[:3]] + \
               [f"🔻 {s}  {c:+.0f}%" for s, c, _, _ in downs[:3]]
        head = f"今天复盘就一句话\n{len(rows)} 个有动静"
        items = rows
        tail = ("我不解释原因 —— 单日变动说明不了什么\n"
                "看的是连续几天的方向\n\n"
                "你更在意单日还是趋势? 👇\n#BinanceAlpha")

    elif vid == 6:    # 自述型 —— 从「一个具体时刻 + 一个对不上的数」讲起, 不从清单讲起
        if nu and nd:
            _a, _b = ups[0], downs[0]
            head = "今天筛池子的时候\n有两个数对不上"
            items = [f"{_a[0]} 一天进 {_a[1]:+.0f}%",
                     f"{_b[0]} 一天撤 {_b[1]:+.0f}%",
                     "同一个池子, 方向相反"]
        elif nu:
            _a = ups[0]
            head = "今天筛池子的时候\n有个数不对劲"
            # nu > 1 时不能说「另外几个一动不动」—— 其他几个也在涨, 只是没这么猛
            _rest = ("其他几个没它这么猛" if nu > 1
                     else "同一个池子里, 另外几个一动不动")
            items = [f"{_a[0]} 持仓 {_a[2]:.1f}M → {_a[3]:.1f}M",
                     f"一天 {_a[1]:+.0f}%",
                     _rest]
        else:
            _b = downs[0]
            head = "今天筛池子的时候\n有个币在往外走"
            # 这个分支 nu==0, 没有「进」可比, 不能说「撤得比进得干脆」
            _rest = ("其他几个没它撤得多" if nd > 1
                     else "同一个池子里, 只有它在动")
            items = [f"{_b[0]} 持仓 {_b[2]:.1f}M → {_b[3]:.1f}M",
                     f"一天 {_b[1]:+.0f}%",
                     _rest]
        tail = ("我说不清这是布局还是接盘\n"
                "单日变动本来就说明不了什么\n\n"
                "我把它记下来了, 明天再看\n\n"
                "你会把这种异动当信号吗? 👇\n#BinanceAlpha")

    else:             # 4 平稳
        head = "今天持仓整体平稳\n没异动"
        items = ["今天 0 个标的变动超过 ±5%", "没异动也是一种信号"]
        tail = ("平静的时候我基本不发\n"
                "但记录还是要记\n\n"
                "你会在意这种「没变化」吗? 👇\n#BinanceAlpha")

    return vid, head, items, _retag(tail, vid, 'alpha')


# ============================================================
# Meme 观察池
# ============================================================

def _m_line(r, medal=''):
    """一行标的：符号(链) + 聪明钱人数 + 集中度 + 市值。"""
    c = r.get('conc')
    c_s = f"集中度{c * 100:.0f}%" if c else "集中度未知"
    mc = r.get('mc')
    mc_s = f"  市值{float(mc) / 1e6:.2f}M$" if mc else ""
    sm = int(r.get('smart') or 0)
    sm_s = f"聪明钱{sm}人  " if sm else ""
    pre = (medal + ' ') if medal else ''
    return f"{pre}{r.get('symbol')} ({str(r.get('chain', '?')).upper()})\n{sm_s}{c_s}{mc_s}"


def _m_row(p):
    """单行标的：符号(链) + 集中度。"""
    c = p.get('conc')
    c_s = f"集中度{c * 100:.0f}%" if c else "集中度未知"
    return f"{p.get('symbol')} ({str(p.get('chain', '?')).upper()})  {c_s}"


def meme_pool(picks):
    """picks: [{'symbol','chain','conc','mc','smart'}]，conc 是小数，mc 是 USD。

    返回 (vid, head, items, tail)。变体由**数据形状**决定：
      没有聪明钱 → 「安静」才成立；市值数据不全 → 不说「市值都 ≤X」。
    """
    n = len(picks)
    mcs = [float(p['mc']) for p in picks if p.get('mc')]
    n_smart = sum(int(p.get('smart') or 0) for p in picks)
    # 市值只有全都有数据时才敢下「都 ≤X」的结论
    mc_s = f", 市值都 ≤{max(mcs) / 1e6:.2f}M$" if mcs and len(mcs) == n else ""

    if n >= 3:
        allowed = [1, 3, 6, 7]
    elif n == 2:
        allowed = [3, 2, 6]
    else:
        allowed = [2]
    if n_smart == 0:
        allowed = allowed + [5]              # 没聪明钱 → 观察型成立
    if n_smart and n > 1:
        allowed = [4] + allowed              # 有聪明钱 → 悬念型成立
    vid = _rotate('meme_pool', allowed)

    if vid == 1:      # 清单型
        if n_smart:
            head = f"我盯的 meme 池里\n聪明钱在买, 筹码还散着{mc_s}"
        else:
            head = f"我盯的 meme 池里\n只有这几个集中度低{mc_s}"
        items = [_m_line(p, ["🥇", "🥈", "🥉"][i]) for i, p in enumerate(picks)]
        tail = "我只列数据, 不喊单\n\n你先看哪个? 👇\n#MemeCoin #Crypto"

    elif vid == 2:    # 聚焦型
        if n_smart:
            p = max(picks, key=lambda x: int(x.get('smart') or 0))
            tail = ("集中度低 + 聪明钱在买\n"
                    "这两条同时满足的不多\n\n"
                    "你会看它吗? 👇\n#MemeCoin #Crypto")
        else:
            p = min(picks, key=lambda x: x.get('conc') if x.get('conc') is not None else 9)
            tail = ("集中度最低的一个\n"
                    "筹码散, 但没人接也是白搭\n\n"
                    "你会看它吗? 👇\n#MemeCoin #Crypto")
        head = f"今天 meme 池我只挑出一个\n{p.get('symbol')} ({str(p.get('chain', '?')).upper()})"
        items = [_m_line(p)]

    elif vid == 3:    # 对比型
        cs = [p['conc'] for p in picks if p.get('conc')]
        gap = (max(cs) - min(cs)) * 100 if len(cs) > 1 else 0
        head = f"同样是低集中度\n这 {n} 个差了 {gap:.0f} 个百分点"
        items = [_m_row(p) for p in picks]
        tail = ("我盯的是集中度\n"
                "但它只是入场券, 不代表会涨\n\n"
                "你更看市值还是集中度? 👇\n#MemeCoin #Crypto")

    elif vid == 4:    # 悬念型 —— 结论由数据决定，不能瞎说
        with_mc = [p for p in picks if p.get('mc')]
        top_smart = max(picks, key=lambda x: int(x.get('smart') or 0))
        min_mc = min(with_mc, key=lambda x: float(x['mc'])) if with_mc else None
        if min_mc is not None and top_smart is min_mc:
            head = f"这 {n} 个里\n有一个聪明钱最多但市值最小"
        else:
            head = f"这 {n} 个里\n聪明钱最多的那个不是市值最小的"
        items = [_m_line(p) for p in picks]
        tail = ("答案在上面, 你自己对一下\n\n"
                "我不直接点名, 免得变成喊单\n\n"
                "你看出来是哪个了吗? 👇\n#MemeCoin #Crypto")

    elif vid == 5:    # 观察型（只在没有聪明钱时出现）
        head = f"今天 meme 池比较安静\n只有 {n} 个集中度够低"
        items = [_m_row(p) for p in picks]
        tail = ("没有聪明钱进场的时候\n"
                "我一般只看不动\n\n"
                "你会空仓等吗? 👇\n#MemeCoin #Crypto")

    elif vid == 6:    # 递进型 —— 按风险排序
        head = "这几个我按风险排了序\n最稳的放前面"
        items = [_m_line(p) for p in picks]
        tail = ("集中度越低越稳\n"
                "但也越没人拉盘\n\n"
                "我一般先看稳的那头\n\n"
                "你会怎么选? 👇\n#MemeCoin #Crypto")

    else:             # 7 失效条件型
        head = "这几个现在数据还行\n但有两种情况我会划掉"
        items = [_m_row(p) for p in picks]
        tail = ("① 集中度突然升高 —— 有人在收筹\n"
                "② 聪明钱开始卖 —— 逻辑就没了\n\n"
                "所以我每天重筛一遍\n\n"
                "你会盯哪个信号? 👇\n#MemeCoin #Crypto")

    return vid, head, items, _retag(tail, vid, 'meme')


# ============================================================
# 聪明钱扫货（钱包池最新链上买入）
# ============================================================

def _mc_str(v):
    """市值人性化：<1M 用 K，否则用 M（0.04M$ 读起来像 0，必须换成 40K$）。"""
    v = float(v)
    return f"{v / 1e3:.0f}K$" if v < 1e6 else f"{v / 1e6:.2f}M$"


def smart_live(picks):
    """picks: [{'tag','mc','n','wallets','ca','amt','qsym'}]，mc 是 USD。

    返回 (vid, head, items, tail)。「动得很轻」只在笔数确实少时才用。

    口径纪律(2026-10-21)：`n` = 成交笔数，`wallets` = 去重钱包数。
    文案里说「N 个地址」时必须用 wallets —— 一个钱包连买 9 笔只算 1 个地址。
    """
    n = len(picks)
    _w = _wallets            # 局部别名，保持下面文案里的写法不变

    mx = max((int(p.get('n') or 0) for p in picks), default=0)   # 变体选择仍按笔数(保持原行为)
    mcs = [float(p['mc']) for p in picks if p.get('mc')]

    if n >= 3 and len(mcs) > 1:
        allowed = [1, 3, 2]        # 清单 / 对比 / 聚焦
    elif n >= 2:
        allowed = [1, 2]
    else:
        allowed = [1]
    if mx <= 2:
        allowed = allowed + [5]    # 笔数确实都少 → 观察型成立
    vid = _rotate('smart_live', allowed)

    def _mc(p):
        return _mc_str(p['mc']) if p.get('mc') else '市值未知'

    if vid == 1:      # 清单型
        head = "链上追踪器刚扫到的货\n全是 5000 万$ 市值以下的"
        items = [f"🟢 {p['tag']}  {_mc(p)}\n"
                 f"{p['n']} 笔买入 {p['amt']:.3g} {p['qsym']}" for p in picks[:4]]
        tail = ("数据 = 币安官方链上追踪器的成交\n"
                "方向由链上转账事件反推, 只留买入\n\n"
                "卖出是获利了结, 买入才是新动作\n"
                "你盯过这个池子吗? 👇\n#Binance #MemeCoin")

    elif vid == 2:    # 聚焦型
        # 按「钱包数」选，笔数只做并列时的次序 —— 「最集中」说的是几个地址，不是几笔
        p = smart_live_focus(picks)
        head = f"今天最集中的一笔链上买入\n{_w(p)} 个地址在买 {p['tag']}"
        items = [f"{p['tag']}  {_mc(p)}\n{p['n']} 笔 {p['amt']:.3g} {p['qsym']}"]
        tail = ("我盯的是重合度\n"
                "同一个币被多个不同地址同时买\n"
                "比单笔大额更值得看\n\n"
                "你怎么看? 👇\n#Binance #MemeCoin")

    elif vid == 3:    # 对比型
        # 极值必须取自**正文里真的列出来的那几个**（picks[:4]）。
        # 原来取全量 picks 的 min/max —— 29 个标的只列 4 个时, head 说的
        # 「市值从 7K$ 到 32.23M$」里那个 7K$ 的币根本不在正文里, 读者查不到。
        shown = picks[:4]
        hi = max(shown, key=lambda x: x['mc'])
        lo = min(shown, key=lambda x: x['mc'])
        head = f"链上追踪器今天扫的货\n市值从 {_mc_str(lo['mc'])} 到 {_mc_str(hi['mc'])}"
        items = [f"🟢 {p['tag']}  {_mc(p)}\n{p['n']} 笔买入" for p in shown]
        tail = ("我盯的是市值分布\n"
                "小市值弹性大, 大市值更稳\n"
                "两头都有人在买\n\n"
                "你偏好哪一头? 👇\n#Binance #MemeCoin")

    else:             # 5 观察型（只在笔数确实少时出现）
        _mxn = max((int(p.get('n') or 0) for p in picks), default=0)
        head = f"链上追踪器今天动得很轻\n最多的一笔也只有 {_mxn} 次买入"
        items = [f"{p['tag']}  {p['n']} 笔  {p['amt']:.3g} {p['qsym']}" for p in picks[:4]]
        tail = ("轻仓试水的时候\n"
                "我一般跟着看, 不跟单\n\n"
                "你会小仓试吗? 👇\n#Binance #MemeCoin")

    return vid, head, items, _retag(tail, vid, 'meme')


def _wallets(p):
    """不同钱包数；老数据没有该字段时退回笔数(不会比以前更差)。"""
    v = p.get('wallets')
    return int(v) if v is not None else int(p.get('n') or 0)


def smart_live_focus(picks):
    """V2「最集中」会聚焦的那个币 —— 按钱包数, 并列时看笔数。

    抽出来共用: 地址跟帖要把它排在最前面, 否则会出现「主帖点名第五名,
    跟帖只放得下前四名」—— 被点名的那个反而拿不到地址。
    """
    if not picks:
        return None
    return max(picks, key=lambda x: (_wallets(x), int(x.get('n') or 0)))


def smart_live_addrs(picks, limit_n=5, focus_first=False):
    """链上追踪器扫货 → 合约地址清单(供 address_thread 用)。

    focus_first=True 时把 V2 会聚焦的那个币排到最前 —— V2 按钱包数选,
    那个币不一定在成交额前几名; 不提前的话主帖点了名、跟帖却放不下它的地址。
    (address_thread 是从尾部落的, 所以顺序 = 重要性。)

    地址用**原样**大小写：SOL 是 base58, 小写化后是另一个不存在的地址。
    没有地址的标的直接跳过, 不编。
    """
    order = list(picks)
    if focus_first:
        focus = smart_live_focus(order)
        if focus is not None:
            order = [focus] + [p for p in order if p is not focus]
    out, _seen = [], set()
    for p in order[:limit_n * 2]:            # 多看几个: 同名被跳过时还能补位
        ca = str(p.get('ca') or '').strip()
        sym = str(p.get('sym') or p.get('ticker') or '').strip().upper()
        if not ca or sym in _seen:
            continue                          # 同名不同合约只留一个
        _seen.add(sym)
        out.append(f"{p.get('sym') or '?'} ({p.get('cn') or '?'}) {ca}")
        if len(out) >= limit_n:
            break
    return out


def addr_covered(main_text, picks, addrs):
    """主帖里点了名、但地址跟帖没覆盖到的币 —— 正常应为空。

    主帖点名却给不出地址, 读者搜 ticker 会买到同名的另一个币
    (本地 pairs 表里 Heyi 33 个合约、Kimchi 233 个), 这正是加地址的理由。

    用词边界匹配, 不做子串 —— 「MemeCoin」里含「Meme」不算提到了 Meme。
    """
    import re as _re
    t = str(main_text or '')
    covered = {str(a).split()[0].lstrip('$').upper() for a in addrs if str(a).strip()}
    miss = []
    for p in picks:
        sym = str(p.get('sym') or p.get('ticker') or '').strip()
        if not sym or sym.upper() in covered:
            continue
        pat = r'(?<![A-Za-z0-9])\$?%s(?![A-Za-z0-9])' % _re.escape(sym)
        if _re.search(pat, t, re.I):
            miss.append(sym)
    return miss


# ============================================================
# 聪明钱买入榜（币安 trading-signal）
# ============================================================

def smart_money(fresh, done, cnt, gain):
    """fresh/done: 信号行列表; cnt(r) 取人数, gain(r) 取涨幅(%)。"""
    nf, nd = len(fresh), len(done)
    tot = nf + nd

    if tot == 0:
        vid = 5                     # 一条信号都没有 —— V1 的 max() 会崩，只能走观察型
    else:
        if nf and nd:
            allowed = [1, 4, 2, 3]
        elif nf:
            allowed = [2, 1, 4]
        else:
            allowed = [3, 1, 4]
        if tot <= 2:
            allowed = allowed + [5]  # 信号确实少 → 观察型成立
        vid = _rotate('smart_money', allowed)

    if vid == 1:      # 分组清单
        hook = max(fresh + done, key=cnt)
        head = f"币安聪明钱信号刚扫货\n{cnt(hook)} 个地址同时买入同一个币"
        items = []
        if fresh:
            blk = ["🟢 刚触发(还没怎么涨)"]
            blk += [f"{r.get('ticker')}  {cnt(r)} 人买入  触发后 {gain(r):+.0f}%"
                    for r in fresh[:2]]
            items.append('\n'.join(blk))
        if done:
            blk = ["🔥 已兑现(信号历史战绩)"]
            blk += [f"{r.get('ticker')}  {cnt(r)} 人买入  触发后 {gain(r):+.0f}%"
                    for r in done[:2]]
            items.append('\n'.join(blk))
        tail = ("聪明钱 = 币安官方信号统计的买入地址数\n"
                "他们买什么, 往往先于市场热度\n\n"
                "我不喊单, 只贴数据\n"
                "你会跟着看哪个? 👇\n#Binance #Crypto")

    elif vid == 2:    # 聚焦刚触发
        r = max(fresh, key=cnt)
        head = f"信号刚出\n{cnt(r)} 个专业地址同时买入 {r.get('ticker')}"
        items = [f"还没怎么涨: 触发后 {gain(r):+.0f}%",
                 "人数越多, 共识越强"]
        tail = ("「刚触发」= 信号刚出、价格还没反应\n"
                "这是我更关注的一组\n\n"
                "你会等它涨了再进, 还是现在看? 👇\n#Binance #Crypto")

    elif vid == 3:    # 聚焦已兑现
        r = max(done, key=gain)
        head = f"这个信号出过之后\n最高 {gain(r):+.0f}%"
        items = [f"{r.get('ticker')}  {cnt(r)} 人买入",
                 "这是历史战绩, 不是现在能买"]
        tail = ("「已兑现」是回看, 不是推荐\n"
                "我看它是为了判断这套信号灵不灵\n\n"
                "你觉得这种回看有意义吗? 👇\n#Binance #Crypto")

    elif vid == 4:    # 对比
        head = "两组信号摆一起\n一组刚出 · 一组已兑现"
        items = [f"🟢 刚触发 {nf} 条\n最高 {max([gain(r) for r in fresh] or [0]):+.0f}%",
                 f"🔥 已兑现 {nd} 条\n最高 {max([gain(r) for r in done] or [0]):+.0f}%"]
        tail = ("我两组都看\n"
                "刚触发看「可能」, 已兑现看「概率」\n\n"
                "这样才不容易被单条带偏\n\n"
                "你先看哪组? 👇\n#Binance #Crypto")

    else:             # 5 观察
        head = f"今天信号很少\n一共 {tot} 条"
        items = [f"🟢 刚触发 {nf} 条 · 🔥 已兑现 {nd} 条",
                 "没信号也是一种信号, 说明专业地址在观望"]
        tail = ("我宁可空着, 也不硬凑一条\n\n"
                "你会等信号, 还是自己找? 👇\n#Binance #Crypto")

    return vid, head, items, _retag(tail, vid, 'meme')


# ============================================================
# 跟帖（thread）
# ============================================================

def _thread_rotate(column, allowed):
    """跟帖的变体轮换 —— 跟主帖分开记状态，互不干扰。"""
    return _rotate(column, allowed)


def filter_thread(criteria, column='filter'):
    """筛选口径型跟帖 —— 4 条栏目共用。

    criteria: [(标题, 说明), ...] 按重要性排序
    返回 (vid, text)。

    5 套结构（角度不同，不是换词）：
      1 逐条列出   —— 是什么
      2 讲取舍     —— 为什么不是更多
      3 讲剔除     —— 什么会被排除
      4 讲演化     —— 这套标准怎么来的
      5 讲验证     —— 筛完还做什么
    """
    n = len(criteria)
    vid = _thread_rotate(column, [1, 2, 3, 4, 5])

    if vid == 1:
        rows = [f"{t}" + (f"\n{d}" if d else "") for t, d in criteria]
        text = (f"我的筛选口径, 一共 {n} 条\n\n"
                + "\n\n".join(rows)
                + "\n\n我按这几条筛, 缺一条就不发\n\n"
                  "你最看重哪一条? 👇")

    elif vid == 2:
        # 「讲原因」的变体只列标题 —— 逐条解释会超 X 的 280 限
        rows = [t for t, _ in criteria]
        text = ("为什么是这几条, 不是别的?\n\n"
                "我试过加更多条件 —— 结果一个都不剩\n\n"
                + "\n\n".join(rows)
                + "\n\n条件不是越严越好, 太严就永远不发帖了\n\n"
                  "你筛的时候会先看哪条? 👇")

    elif vid == 3:
        # 标题**不能丢**。原来写的是 `d or t` —— 说明代替了标题，读者看到的是
        # 「过不了任意一条的，我直接跳过：有人真金白银进场」：本意是入选条件，
        # 在「我跳过」的语境下意思正好反了。
        # 但「标题+说明」4 条实测 281/280 差 1 字，所以 head 同步砍短
        # （去掉「下面」和 `**` —— X 上 ** 本来也不渲染成粗体）。砍完 271/280。
        rows = [f"{t}\n{d}" if d else t for t, d in criteria]
        text = (f"这套口径一共 {n} 条, 主要是用来剔除的\n\n"
                "过不了任意一条的, 我直接跳过:\n\n"
                + "\n\n".join(rows)
                + "\n\n留下来的才有资格进榜\n\n"
                  "你觉得该不该再严一点? 👇")

    elif vid == 4:
        rows = [t for t, _ in criteria]
        text = ("我这套口径不是一开始就有的\n\n"
                "最早的版本只有一条, 被坑了几次才加到现在:\n\n"
                + "\n\n".join(rows)
                + "\n\n每条背后都是一次踩坑\n\n"
                  "你有过类似的教训吗? 👇")

    else:
        rows = [f"{t}" + (f"\n{d}" if d else "") for t, d in criteria]
        text = ("筛完不算完, 我会自己再核一遍\n\n"
                + "\n\n".join(rows)
                + "\n\n核过的才会发出来\n\n"
                  "你自己会怎么核? 👇")

    return vid, text


def review_thread(ups, downs, column='review_thread'):
    """每日复盘跟帖 —— 持仓 vs 价格怎么读。返回 (vid, text)。"""
    nu, nd = len(ups), len(downs)
    if nu and nd:
        allowed = [1, 2, 3, 4]
    elif nu or nd:
        allowed = [1, 2, 3]
    else:
        allowed = [1, 2]
    vid = _thread_rotate(column, allowed)

    if vid == 1:
        text = ("这两个数字怎么看?\n\n"
                "持仓变动 ≥5% 才进这个榜\n\n"
                "持仓涨 + 价格横盘 = 主力在悄悄建仓\n\n"
                "持仓涨 + 价格急拉 = 已经启动, 波动会很大\n\n"
                "持仓跌 = 资金走了, 别去接飞刀\n\n"
                "我一般先看价格有没有跟着动。\n\n"
                "你看到的是哪一种? 👇")

    elif vid == 2:
        text = ("持仓涨 ≠ 要涨\n\n"
                "关键看价格跟不跟:\n\n"
                "价格横盘 + 持仓涨 = 还在建仓\n\n"
                "价格已经拉起来 = 大概率晚了\n\n"
                "我只用这一个区分。\n\n"
                "门槛是 5%, 低于这个数不进榜。\n\n"
                "你更怕错过, 还是更怕接盘? 👇")

    elif vid == 3:
        text = ("先问一句: 持仓涨了, 该不该跟?\n\n"
                "我的答案是不一定 —— 要看价格在哪个位置\n\n"
                "持仓变动 ≥5% 我才看, 低于这个数不进榜\n\n"
                "横盘期进的, 和拉起来才发现的, 是两回事\n\n"
                "我一般只在前者动手。\n\n"
                "你怎么判断早晚? 👇")

    else:
        rows = []
        if nu:
            rows.append(f"🔺 {nu} 个加仓的 —— 看价格有没有跟上")
        if nd:
            rows.append(f"🔻 {nd} 个撤退的 —— 看是不是集体行为")
        text = ("今天上榜的, 我分两类看\n\n"
                + "\n\n".join(rows)
                + "\n\n单日变动说明不了什么, 要看连续几天的方向\n\n"
                  "你会连着看几天? 👇")

    return vid, text


def money_thread(fresh, done, cnt, gain, column='money_thread'):
    """聪明钱买入榜跟帖 —— 刚触发 vs 已兑现。返回 (vid, text)。"""
    nf, nd = len(fresh), len(done)
    if nf and nd:
        allowed = [1, 4, 2, 3]
    elif nf:
        allowed = [2, 1, 4]
    elif nd:
        allowed = [3, 1, 4]
    else:
        allowed = [1]
    vid = _thread_rotate(column, allowed)

    _fmax = max([gain(r) for r in fresh] or [0])
    _dmax = max([gain(r) for r in done] or [0])
    best = max(_fmax, _dmax)

    if vid == 1:
        text = ("我盯这两组的方式\n\n"
                "🟢 刚触发 = 信号刚出、还没怎么涨\n\n"
                f"最高 {_fmax:+.0f}%\n\n"
                "🔥 已兑现 = 信号出过之后的涨幅\n\n"
                f"最高 {_dmax:+.0f}%\n\n"
                f"我看到的最高一条: {best:+.0f}%\n\n"
                "人数 = 多少个专业地址同时买它\n\n"
                "你觉得哪组更值得看? 👇")

    elif vid == 2:
        text = ("「刚触发」怎么用?\n\n"
                "信号刚出、价格还没反应 —— 这是我更关注的一组\n\n"
                f"今天最高 {_fmax:+.0f}%\n\n"
                "但不代表要立刻买: 人数少的时候可能只是噪音\n\n"
                "我看的是「人数 × 涨幅」的组合\n\n"
                "你会给自己设一个门槛吗? 👇")

    elif vid == 3:
        text = ("「已兑现」不是推荐, 是回看\n\n"
                "它唯一的作用是: 判断这套信号灵不灵\n\n"
                f"最高到过 {_dmax:+.0f}%\n\n"
                "但这不代表下一个也能\n\n"
                "我拿它校准, 不拿它下单\n\n"
                "你会参考历史战绩吗? 👇")

    else:
        text = ("两组摆一起看, 才不容易被单条带偏\n\n"
                "🟢 刚触发看的是「可能」\n\n"
                "🔥 已兑现看的是「概率」\n\n"
                f"我看到的最高一条: {best:+.0f}%\n\n"
                "可能 × 概率, 才是一个信号的完整样子\n\n"
                "我两组都记, 但只对刚触发的那组有动作\n\n"
                "你先看哪组? 👇")

    return vid, text


def address_thread(addrs, xlen_fn, limit, tail, column='address_thread'):
    """合约地址清单跟帖 —— 数据固定，只换开场和收尾。

    addrs 逐条加，保证整条 <= limit（X 加权）。返回 (vid, text)。
    """
    OPENS = [
        "我自己会再核一遍\n合约地址(可自行查证):\n",
        "地址我放这儿, 你自己查\n\n",
        "下面这些我都在链上核过:\n\n",
        "别只信我说的, 自己核一遍\n\n",
        "我留个底, 方便你复核:\n\n",
    ]
    vid = _thread_rotate(column, [1, 2, 3, 4, 5])
    open_line = OPENS[vid - 1]

    picked = []
    for a in addrs:
        cand = open_line + "\n".join(picked + [a]) + tail
        if xlen_fn(cand) <= limit:
            picked.append(a)
    if not picked:
        return vid, ("链上数据筛选, 不代表必涨, 不构成投资建议。\n"
                     "每天更新链上聪明钱动向。")
    return vid, open_line + "\n".join(picked) + tail

