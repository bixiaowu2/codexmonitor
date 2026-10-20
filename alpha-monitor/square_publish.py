#!/usr/bin/env python3
"""币安广场发帖(官方 OpenAPI)。

复用雷达数据组装"妖币观察池"/"每日复盘", 配图后经官方 square-post skill 发布。
官方接口每日上限 100 帖, 全自动无需人工, 零封号风险。
用法:
  square_publish.py --scan     发"妖币观察池"(图文)
  square_publish.py --review   发"每日复盘"(图文)
  square_publish.py --text "…" 发纯文字
  square_publish.py --test     发测试帖
"""
from __future__ import annotations
import json, os, subprocess, time, argparse
from pathlib import Path

DATA = Path('/var/lib/demon-radar')
CHARTS = Path('/var/lib/x-publisher/charts')
SKILL = Path('/opt/square-post')
BUN = '/home/ubuntu/.bun/bin/bun'
KEYFILE = Path('/etc/square-post.env')




def _oi(m):
    """baseline.json 的字段名中途从 `oi` 改成了 `oi_notional`, 旧文件仍是 `oi`。
    两处 review 只读 `oi_notional` → 旧 baseline 全读到 0 → 复盘永远"无可发内容"。
    这里兼容两种名字。"""
    return m.get('oi_notional') or m.get('oi') or 0

def api_key():
    k = os.environ.get('BINANCE_SQUARE_OPENAPI_KEY', '').strip()
    if k:
        return k
    if KEYFILE.exists():
        for line in KEYFILE.read_text(encoding='utf-8').splitlines():
            if line.startswith('BINANCE_SQUARE_OPENAPI_KEY='):
                return line.split('=', 1)[1].strip()
    return ''


def run_script(script, args, timeout=180):
    env = dict(os.environ)
    env['BINANCE_SQUARE_OPENAPI_KEY'] = api_key()
    r = subprocess.run([BUN, str(SKILL / 'scripts' / script)] + args,
                       cwd=str(SKILL), capture_output=True, text=True,
                       timeout=timeout, env=env)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def _relay_square(text, kind='帖子'):
    """广场发帖成功后，同步到群（内容 bot）。失败不影响主流程。"""
    try:
        import sys as _s
        _s.path.insert(0, '/opt/content-push')
        import relay
        body = f'📮 [广场] 已自动发布 · {kind}\n\n' + text[:900]
        relay.send(body)
        print('  已同步到群')
    except Exception as e:
        print(f'  同步到群失败: {str(e)[:80]}')


def post_text(text, title=None):
    import post_style as _ps
    for _w in _ps.audit(text, platform='square'):
        print(f'[风格告警·square] {_w}')
    args = ['--text', text]
    if title:
        args += ['--title', title]
    code, out = run_script('post-text.mjs', args)
    if 'Success' in (out or ''):
        _relay_square(text)
    return code, out


def post_image(text, images):
    import post_style as _ps
    for _w in _ps.audit(text, platform='square'):
        print(f'[风格告警·square] {_w}')
    code, out = run_script('post-image.mjs', ['--text', text, '--images', ','.join(images)])
    if 'Success' in (out or ''):
        _relay_square(text, '图文帖')
    return code, out


def cashtag(symbol):
    """币安广场 cashtag: $SYMBOL(大写标准格式)。

    规则(宁缺毋滥——错误的 cashtag 比没有更糟, 会指向不存在的币):
      - 非 ASCII(中文/韩文/emoji/Cyrillic 混写) 无法成 cashtag → 原样返回
      - 含空格/连字符等非字母数字 → 原样返回(不做清洗! 清洗会造出别的符号)
      - 纯数字 → 原样返回
      - 合法: 纯字母数字且含字母, 长度 1-15 → 统一大写, 加 $
    """
    sym = str(symbol or '').strip()
    if not sym or not sym.isascii():
        return sym
    if not sym.isalnum():
        return sym
    if not any(c.isalpha() for c in sym):
        return sym
    if len(sym) > 15:
        return sym
    return '$' + sym.upper()


_ALPHA_CA = None


def alpha_contracts():
    """币安 Alpha 代币的合约地址集合(缓存 1 次/进程)。

    为什么需要: 聪明钱/链上信号的 ticker 是 **DEX 代币**, 不是币安上币符号。
    直接加 $ 会让 `$DOGE` 指向币安上的**狗狗币** —— 同名但完全不同的币。
    错误的 cashtag 比没有更糟(会误导读者去点错币), 所以必须用**合约地址**核对:
    只有该 DEX 代币的合约地址确实出现在币安 Alpha 名录里, 才认为它是同一个币、才加 $。
    """
    global _ALPHA_CA
    if _ALPHA_CA is not None:
        return _ALPHA_CA
    out = set()
    try:
        import sys as _sys
        _sys.path.insert(0, '/opt/alpha-radar')
        from radar import PublicAPI, TOKENS
        from binance_official import token_list
        api = PublicAPI('/var/lib/demon-radar/bnsym-cache', ttl=1800)
        tokens, _ = token_list(api.data(TOKENS, {}))
        for t in tokens:
            ca = str(t.get('contractAddress') or '').strip().lower()
            if ca:
                out.add(ca)
    except Exception:
        pass
    _ALPHA_CA = out
    return out


def safe_cashtag(symbol, contract):
    """只有**合约地址**对得上币安 Alpha 名录时才加 $; 否则原样返回符号。
    (仅按 ticker 判断是不够的 —— 同名不同币太常见。)"""
    ca = str(contract or '').strip().lower()
    if ca and ca in alpha_contracts():
        return cashtag(symbol)
    return str(symbol or '')


def compose_scan():
    """观察池: 文字 + 集中度/持仓图。风格见 post_style。"""
    import post_style as ps
    try:
        m = json.loads((DATA / 'matches.json').read_text())
    except Exception:
        return None, []
    sa = [x for x in m if x.get('tier') in ('S', 'A')]
    if not sa:
        return None, []
    try:
        import sys
        sys.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_scan()
    except Exception:
        chart = None
    picks = sa[:3]
    head = f"我扒了全部币安Alpha代币\n只有 {len(picks)} 个同时过三道筛"
    items = [f"{'🥇🥈🥉'[i]} {safe_cashtag(x['symbol'], x.get('address'))}  控盘 {x['cleaned_top10']*100:.0f}%\n"
             f"合约持仓 {x['oi_notional']/1e6:.1f}M USDT"
             for i, x in enumerate(picks)]
    tail = ("三道筛: 已上合约 · 未上现货 · 大户控盘≥60%\n"
            "现货上市前, 是资金布局的窗口\n\n"
            "这三个你先看哪个?\n\n"
            "点击币种可看实时行情与合约数据。\n"
            "数据筛选, 不构成投资建议。")
    text = ps.compose(head, items, tail, limit=2000)
    imgs = [str(chart)] if chart and Path(chart).exists() else []
    return text, imgs


def compose_review():
    """复盘: 文字 + 持仓变化图。风格见 post_style。"""
    import post_style as ps
    try:
        b = json.loads((DATA / 'baseline.json').read_text())
        t = json.loads((DATA / 'track_state.json').read_text())
    except Exception:
        return None, []
    try:
        import sys
        sys.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_review()
    except Exception:
        chart = None
    ups, downs = [], []
    for m in b.get('matches', [])[:5]:
        sym = m['symbol']
        cur = t.get('oi_snap', {}).get(sym) or _oi(m)
        base = _oi(m)
        if base > 0:
            chg = (cur / base - 1) * 100
            if chg >= 5:
                ups.append((sym, chg, base / 1e6, cur / 1e6, m.get('address')))
            elif chg <= -5:
                downs.append((sym, chg, base / 1e6, cur / 1e6, m.get('address')))
    n_up, n_down = len(ups), len(downs)
    if n_up and not n_down:
        head = f"{n_up} 个Alpha币被资金加仓\n钱在往哪走?"
    elif n_down and not n_up:
        head = f"{n_down} 个Alpha币遭资金撤退\n谁在被抛弃?"
    elif n_up and n_down:
        head = f"复盘: {n_up} 个加仓 · {n_down} 个撤退\n钱在换仓"
    else:
        head = "复盘: 持仓整体平稳\n没异动, 也是一种信号"
    # 币安广场单帖 cashtag 服务端上限 3 个(超出报 220095 Coin pair count), 按剩余名额挂。
    used = 0
    items = []
    for sym, chg, b0, c0, addr in (ups[:2] + downs[:1])[:3]:
        arrow = '🔺' if chg > 0 else '🔻'
        tag = safe_cashtag(sym, addr)          # 用该币自己的合约地址, 不是循环泄漏的 m
        if tag.startswith('$'):
            used += 1
        items.append(f"{arrow} {tag}  {chg:+.0f}%\n{b0:.1f}M → {c0:.1f}M USDT")
    if not items:
        items.append("较上一基线无 ±5% 以上变动")
    try:
        cur = json.loads((DATA / 'matches.json').read_text())
    except Exception:
        cur = []
    if cur:
        pool = ["📌 当前观察池"]
        for m in cur[:5]:
            cand = safe_cashtag(m['symbol'], m.get('address'))
            if cand.startswith('$'):
                if used >= 3:
                    cand = m['symbol']      # 名额用完, 退回纯符号
                else:
                    used += 1
            ct = m.get('cleaned_top10')
            ct_s = f"  控盘 {ct*100:.0f}%" if ct else ''
            pool.append(f"{cand}  持仓 {_oi(m)/1e6:.1f}M USDT{ct_s}")
        items.append('\n'.join(pool))
    tail = ("持仓上升 = 资金在布局\n"
            "持仓下降 = 资金在撤退\n\n"
            "明天早帖给新的观察池。\n"
            "数据筛选, 不构成投资建议。")
    text = ps.compose(head, items, tail, limit=2000)
    imgs = [str(chart)] if chart and Path(chart).exists() else []
    return text, imgs


SIGNAL_STATE = Path('/var/lib/demon-radar/square_posted.json')
SIGNAL_QUEUE = Path('/var/lib/demon-radar/square_queue.jsonl')

SIGNAL_TITLE = {
    '启动': '🚀 启动信号',
    '回撤': '⚠️ 回撤预警',
    '止盈1': '💰 第一档止盈',
    '止盈2': '💰 第二档止盈',
    '持仓': '📈 持仓异动',
}


def _num(v):
    """安全转 float；拿不到就返回 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _signal_body(ev, tag):
    """按信号类型生成结构化文案。

    优先用事件里的结构化字段；字段缺失(旧事件)返回 None, 由调用方退回原行为。
    风格依据 post_style: 首行钩子 / 第一人称 / 具体数字 / 结尾开放循环。
    """
    kind = ev.get('kind', '')

    if kind == '启动':
        rate = _num(ev.get('rate'))
        if rate is None:
            return None
        return (
            f"{tag} 资金费率刚转负 {rate * 100:.2f}%\n"
            "\n"
            "空头开始付钱给多头。\n"
            "\n"
            "我盯的 Alpha 池里\n"
            "这是最新一个转负的\n"
            "\n"
            "费率转负不等于马上涨\n"
            "它说明多空分歧在加大\n"
            "\n"
            "这种时候你会上车吗？👇"
        )

    if kind == '回撤':
        peak = _num(ev.get('peak'))
        last = _num(ev.get('last'))
        dd = _num(ev.get('drop_pct'))
        if peak is None or last is None or dd is None:
            return None
        return (
            f"{tag} 三天从 {peak:.6g} 到 {last:.6g}\n"
            f"回撤 {dd * 100:.1f}%\n"
            "\n"
            "这个速度不是回调\n"
            "是杠杆在连环爆\n"
            "\n"
            "我盯的池子里\n"
            "它是跌得最快的一个\n"
            "\n"
            "快跌之后通常两种走法：\n"
            "横盘消化，或者继续瀑布\n"
            "\n"
            "区别在今晚的成交量\n"
            "你怎么看？👇"
        )

    if kind in ('止盈1', '止盈2'):
        mult = _num(ev.get('mult'))
        price = _num(ev.get('price'))
        if mult is None:
            return None
        tier = '第一档' if kind == '止盈1' else '第二档'
        px = f"现价 {price:.6g}\n" if price is not None else ''
        return (
            f"{tag} 较基准价已经 {mult:.1f}x\n"
            f"{px}"
            "\n"
            f"我的{tier}止盈信号触发了\n"
            "\n"
            "翻倍之后最容易犯的错\n"
            "是舍不得卖\n"
            "\n"
            "分批止盈是纪律\n"
            "不是判断 ——\n"
            "\n"
            "你会怎么处理这一档？👇"
        )

    if kind == '持仓':
        oi = _num(ev.get('oi'))
        prev = _num(ev.get('prev'))
        if oi is None or not prev:
            return None
        return (
            f"{tag} 合约持仓 {oi / 1e6:.1f}M\n"
            f"较前期 {prev / 1e6:.1f}M 增 {oi / prev - 1:+.1%}\n"
            "\n"
            "钱在往这个币里加。\n"
            "\n"
            "我看的是持仓增速\n"
            "不是价格\n"
            "\n"
            "持仓先动，价格后动 ——\n"
            "\n"
            "你盯过这个信号吗？👇"
        )

    return None


def compose_signal(ev):
    """把 demon-track 的信号事件转成广场帖(带 cashtag + 走势图)。

    有结构化字段时走 _signal_body（第一人称 + 钩子 + 开放循环）;
    没有(旧事件)则退回原来的「标题行 + 原文案」。
    """
    kind = ev.get('kind', '其他')
    sym = ev.get('symbol', '')
    futs = ev.get('futures', sym + 'USDT')
    tag = safe_cashtag(sym, ev.get('address'))   # 事件里的地址在 ev 上, 这里没有 m

    body = _signal_body(ev, tag)
    if body is None:
        title = SIGNAL_TITLE.get(kind, '📊 雷达信号')
        raw = str(ev.get('text', ''))
        # 去掉内部标题行(第一行), 保留说明部分
        old = raw.split('\n', 1)[1].strip() if '\n' in raw else raw
        body = f'{title} · {tag}\n{old}'

    lines = [body, '', '点击上方币种可查看实时行情。数据筛选, 不构成投资建议。']
    text = '\n\n'.join(x for x in lines if x)
    chart = None
    try:
        import sys
        sys.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_signal(sym, futs)
    except Exception:
        chart = None
    imgs = [str(chart)] if chart and Path(chart).exists() else []
    return text, imgs


def post_signals(max_posts=3):
    """消费信号队列, 每次最多发 max_posts 条。"""
    if not SIGNAL_QUEUE.exists():
        print('无信号队列')
        return
    try:
        posted = set(json.loads(SIGNAL_STATE.read_text())) if SIGNAL_STATE.exists() else set()
    except Exception:
        posted = set()
    events = []
    for line in SIGNAL_QUEUE.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        key = f"{ev.get('kind')}:{ev.get('symbol')}:{int(ev.get('ts', 0))}"
        if key in posted:
            continue
        events.append((key, ev))
    if not events:
        print('无新信号')
        return
    # 只发最近的(避免积压刷屏)
    events = events[-max_posts:]
    sent = 0
    for key, ev in events:
        text, imgs = compose_signal(ev)
        if imgs:
            code, out = post_image(text, imgs)
        else:
            code, out = post_text(text)
        if 'Success' in out:
            posted.add(key)
            sent += 1
            print(f"[OK] {ev.get('kind')} {ev.get('symbol')}")
        else:
            print(f"[FAIL] {ev.get('kind')} {ev.get('symbol')}: {out.strip()[:150]}")
            break  # 失败就停, 下轮重试
    SIGNAL_STATE.write_text(json.dumps(sorted(posted), ensure_ascii=False))
    print(f'本轮发送 {sent} 条信号帖')


def smart_live_picks(max_mc=50e6, size=50):
    """自建钱包池的最新链上**买入**, 已按成交额降序。返回 [{sym, cn, qsym, amt, mc, tag}]。

    数据源: 币安官方链上追踪 address-tracker/trades (trackerType=1 = 我们建的钱包池)。
    方向: 只保留买入 —— tradeType 3/5 已用链上 Transfer 事件反推确认(见 T3.1)。
    市值上限: 读者要的是"还没涨起来的", 报一个 35 亿的成熟币没意义。
    """
    import sys as _sys
    _sys.path.insert(0, '/opt/alpha-radar')
    try:
        import web3_client as _w3
    except Exception:
        return []

    TT_BUY = {'3', '5'}
    CH = {'56': ('BSC', 'BNB'), 'CT_501': ('SOL', 'SOL')}
    seen_tx = set()
    agg = {}
    for cid, (cn, qsym) in CH.items():
        try:
            d = _w3.call('GET', '/api/v1/dex/market/address-tracker/trades',
                         {'binanceChainId': cid, 'trackerType': '1', 'page': 1, 'size': size})
        except Exception:
            continue
        for t in (d or {}).get('trades') or []:
            if str(t.get('tradeType')) not in TT_BUY:
                continue
            tx = str(t.get('txHash') or '')
            if tx:
                if tx in seen_tx:          # 接口会给重复行, 不去重会重复计数
                    continue
                seen_tx.add(tx)
            amt = _f(t.get('quoteTokenAmount'))
            mc = _f(t.get('marketCap'))
            if not amt or not mc or mc > max_mc:
                continue
            ca = str(t.get('tokenContractAddress') or '').lower()
            sym = t.get('tokenSymbol') or '?'
            k = ca or f'{cn}:{sym.lower()}'
            if k in agg:
                agg[k]['amt'] += amt
                agg[k]['n'] += 1
                agg[k]['mc'] = mc          # 市值取最新
            else:
                agg[k] = {'sym': sym, 'cn': cn, 'qsym': qsym, 'amt': amt, 'n': 1, 'mc': mc,
                          'tag': safe_cashtag(sym, t.get('tokenContractAddress'))}
    out = list(agg.values())
    out.sort(key=lambda x: -x['amt'])
    return out


def compose_smart_live():
    """新栏目「聪明钱刚买了什么」—— 来自币安官方链上追踪(address-tracker/trades)。

    与已有的「聪明钱买入榜」不同: 那个是 trading-signal 的每笔信号;
    这个是**我们自建的钱包池**(币安排行榜筛出: 胜率>=55% 且 已实现盈亏>=1万$ 且 交易数>=20)
    的**最新链上成交**, 且带买卖方向(方向由链上 Transfer 事件反推:
    钱包在 to=买入 / 在 from=卖出)。

    只取买入 —— "聪明钱刚买了什么"才有信息量; 卖出是他们的获利了结, 对读者参考价值低。
    """
    import post_style as ps
    picks = smart_live_picks()
    if not picks:
        return None, []
    head = "我盯的钱包池刚扫货\n全是 5000 万$ 市值以下的"
    items = [f"🟢 {x['tag']}  {x['mc']/1e6:.2f}M$\n{x['n']} 笔买入 {x['amt']:.3g} {x['qsym']}"
             for x in picks[:4]]
    tail = ("钱包池 = 币安官方排行榜筛出的实盘赢家\n"
            "胜率≥55% · 已实现盈亏≥1万$ · 交易数≥20\n\n"
            "只列买入 —— 他们卖出是获利了结。\n"
            "数据筛选, 不构成投资建议。")
    return ps.compose(head, items, tail, limit=2000), []


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def smart_money_picks():
    """币安 trading-signal 的聪明钱**买入**信号 → 按币聚合。

    返回 {'fresh': [...], 'done': [...], 'hook': {...}} 或 None。
    fresh = 刚触发还没怎么涨的; done = 已兑现(信号历史战绩)。
    抽出来供两个 composer 共用(广场版 + X 版), 数据逻辑只写一份。
    """
    try:
        import bn_skills
    except Exception:
        return None
    rows = []
    for chain, cid in (('bsc', bn_skills.CHAIN_BSC), ('solana', bn_skills.CHAIN_SOL)):
        try:
            for r in bn_skills.smart_money(chain_id=cid, page_size=100):
                rows.append(r)
        except Exception:
            continue
    buys = [r for r in rows if str(r.get('direction', '')).lower() == 'buy']
    if not buys:
        return None

    def gain(r):
        ap = bn_skills.num(r.get('alertPrice'))
        cp = bn_skills.num(r.get('currentPrice'))
        if ap and cp and ap > 0:
            return (cp / ap - 1) * 100
        return None

    def cnt(r):
        return int(bn_skills.num(r.get('smartMoneyCount')) or 0)

    best = {}
    for r in buys:
        t = str(r.get('ticker') or '').strip()
        if not t:
            continue
        k = t.lower()
        if k not in best or cnt(r) > cnt(best[k]):
            best[k] = r
    pool = list(best.values())

    fresh, done = [], []
    for r in pool:
        g = gain(r)
        if g is None:
            continue
        if g >= 25:
            done.append(r)
        elif g >= -10:
            fresh.append(r)
    fresh.sort(key=lambda r: -cnt(r))
    done.sort(key=lambda r: -(gain(r) or 0))

    hook = fresh[0] if fresh else (done[0] if done else (pool[0] if pool else None))
    if hook is None:
        return None
    return {'fresh': fresh, 'done': done, 'hook': hook, 'cnt': cnt, 'gain': gain}


def compose_smart_money():
    """广场版: 注意 cashtag 服务端上限 3 个(实测>3 报 220095)。风格见 post_style。"""
    import post_style as ps
    d = smart_money_picks()
    if not d:
        return None, []
    fresh, done, hook, cnt, gain = d['fresh'], d['done'], d['hook'], d['cnt'], d['gain']
    head = f"聪明钱刚扫货\n{cnt(hook)} 个地址同时买入同一个币"
    used = 0
    items = []
    if fresh:
        block = ["🟢 刚触发(还没怎么涨)"]
        for r in fresh[:2]:
            if used >= 3:
                break
            block.append(f"{safe_cashtag(r.get('ticker'), r.get('contractAddress'))}  "
                         f"{cnt(r)} 人买入  触发后 {gain(r):+.0f}%")
            used += 1
        items.append('\n'.join(block))
    if done and used < 3:
        block = ["🔥 已兑现(信号历史战绩)"]
        for r in done[:3 - used]:
            block.append(f"{safe_cashtag(r.get('ticker'), r.get('contractAddress'))}  "
                         f"{cnt(r)} 人买入  触发后 {gain(r):+.0f}%")
            used += 1
        items.append('\n'.join(block))
    tail = ("聪明钱 = 链上被持续跟踪的专业地址\n"
            "他们买什么, 往往先于市场热度\n\n"
            "数据筛选, 不构成投资建议。")
    return ps.compose(head, items, tail, limit=2000), []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', action='store_true')
    ap.add_argument('--review', action='store_true')
    ap.add_argument('--test', action='store_true')
    ap.add_argument('--signal', action='store_true', help='消费信号队列发广场')
    ap.add_argument('--smart-money', action='store_true', help='发聪明钱买入榜')
    ap.add_argument('--smart-live', action='store_true', help='发聪明钱最新买入(链上追踪)')
    ap.add_argument('--text', type=str)
    args = ap.parse_args()

    if not api_key():
        print('缺少 BINANCE_SQUARE_OPENAPI_KEY(env 或 /etc/square-post.env)')
        return

    if getattr(args, 'smart_live', False):
        text, imgs = compose_smart_live()
        if not text:
            print('无聪明钱买入记录'); return
        code, out = post_text(text)
        print('Success' if 'Success' in out else out.strip()[:200])
        return

    if args.smart_money:
        text, imgs = compose_smart_money()
        if not text:
            print('无聪明钱数据')
            return
        code, out = post_text(text)
        print(out)
        return
    if args.signal:
        post_signals()
        return
    if args.test:
        code, out = post_text('币安Alpha雷达 · 连接测试 ✅\n后续将日更妖币观察池与复盘。数据筛选, 不构成投资建议。')
        print(out)
        return
    if args.text:
        code, out = post_text(args.text)
        print(out)
        return

    text = imgs = None
    if args.scan:
        text, imgs = compose_scan()
    elif args.review:
        text, imgs = compose_review()
    if not text:
        print('无可发内容(数据不足或候选为空)')
        return

    if imgs:
        code, out = post_image(text, imgs)
    else:
        code, out = post_text(text)
    print(out)


if __name__ == '__main__':
    main()
