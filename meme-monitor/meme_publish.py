#!/usr/bin/env python3
"""Meme 币发帖器。

内容: meme 观察池(S/A级) + 聪明钱买入榜。
渠道: 默认只推 Telegram 带图草稿(人工发 X); 币安广场默认关闭(加 --square 才发)。
草稿结构对齐 x_publisher 的妖币套路, 并把跟帖扩成完整 Thread 串(涨流量):
  主帖(钩子+TOP3+筛选+hashtag, ≤280字) → 跟帖1(筛选逻辑) → 跟帖2(合约地址+风险)
注意: X 串是**手动**发的 —— 本脚本只出稿, post_tweet 不支持 in_reply_to。

cashtag 策略: meme 多是 DEX 代币, 未上币安 → cashtag 不会链接。
故仅对"币安已上币"的符号加 cashtag, 其余一律用**合约地址**(唯一可靠标识)。
"""
from __future__ import annotations
import json, os, sys, time, argparse
from pathlib import Path

sys.path.insert(0, '/opt/alpha-radar')
DATA = Path('/var/lib/meme-radar')
XMATCH = DATA / 'meme_matches.json'
SQUARE_POST = '/opt/alpha-radar/square_publish.py'
AK_PY = '/opt/akshare-venv/bin/python3'

_BINANCE_SYMS = None


def secret(env, name):
    p = env.get(name + '_FILE')
    return Path(p).read_text().strip() if p else env.get(name, '').strip()


def binance_symbols():
    """币安 Alpha/合约已上币的符号集(用于判断 cashtag 是否有效)。缓存 1 小时。"""
    global _BINANCE_SYMS
    cache = DATA / 'binance_symbols.json'
    try:
        if cache.exists() and time.time() - cache.stat().st_mtime < 3600:
            return set(json.loads(cache.read_text()))
    except Exception:
        pass
    out = set()
    try:
        sys.path.insert(0, '/opt/meme-radar')
        from radar import PublicAPI, TOKENS
        from binance_official import token_list
        api = PublicAPI('/var/lib/meme-radar/bnmatch-cache', ttl=600)
        tokens, _ = token_list(api.data(TOKENS, {}))
        out = {str(t.get('symbol', '')).upper() for t in tokens if t.get('symbol')}
        cache.write_text(json.dumps(sorted(out)))
    except Exception:
        pass
    return out


def tag_for(sym):
    """币安已上币才加 cashtag; 否则不加(避免无效链接)。"""
    if not sym or not str(sym).isascii():
        return str(sym or '?')
    s = str(sym).strip().upper()
    if s in (_BINANCE_SYMS or set()):
        return f'${s}'
    return str(sym)


def load_matches():
    try:
        return json.loads(XMATCH.read_text(encoding='utf-8'))
    except Exception:
        return []


def tg_send_photo(path, caption=""):
    """用 meme 自己的 bot 发图(配图草稿)。"""
    import urllib.request, uuid
    token = secret(os.environ, 'MEME_TELEGRAM_BOT_TOKEN')
    chat = (secret(os.environ, 'MEME_DRAFT_CHAT_ID')
            or secret(os.environ, 'MEME_TELEGRAM_CHAT_ID'))
    p = Path(path)
    if not token or not chat or not p.exists():
        return False
    boundary = uuid.uuid4().hex
    fields = {'chat_id': chat}
    if caption:
        fields['caption'] = caption[:1000]
    body = b''
    for k, v in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{p.name}"\r\n'
             f'Content-Type: image/png\r\n\r\n').encode()
    body += p.read_bytes() + b'\r\n' + (f'--{boundary}--\r\n').encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendPhoto',
                                 data=body,
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return bool(r.get('ok'))
    except Exception as e:
        print('发图失败:', str(e)[:100])
        return False


MAX_TWEET_LEN = 280   # X 单帖上限(与 x_publisher.py 一致)


import sys as _sys
_sys.path.insert(0, '/opt/alpha-radar')
try:
    import post_style as ps
except Exception:                     # 风格模块缺失时退化, 不能因此发不出帖
    class ps:                         # type: ignore
        X_LIMIT = 280

        @staticmethod
        def xlen(t):
            return sum(2 if ord(c) > 0x1100 else 1 for c in t)

        @staticmethod
        def compose(head, items, tail=None, limit=280):
            return '\n\n'.join([head] + [i for i in items if i] + ([tail] if tail else []))


def compose_scan():
    """meme 观察池帖(钩子主帖 + Thread跟帖 + 配图)。风格见 post_style。

    长度一律用 ps.xlen(X 加权, 中文算 2) —— 原来的 len() 是字符数, 会漏判。
    """
    rows = load_matches()
    global _BINANCE_SYMS
    _BINANCE_SYMS = binance_symbols()
    sa = [r for r in rows if r.get('tier') in ('S', 'A')]
    # 与 meme_scan 的 S/A 排序一致: S 优先 → 低集中度优先 → 小市值优先 → 聪明钱多优先。
    sa.sort(key=lambda r: (0 if r.get('tier') == 'S' else 1,
                           (r.get('cleaned_top10') if r.get('cleaned_top10') is not None else 9),
                           (r.get('market_cap') or 9e18),
                           -(r.get('smart') or 0)))
    if not sa:
        return None

    picks = sa[:3]
    # 钩子必须与数据一致 —— 排序把 S 级(按评分/成交量进的)排在前, 它可能聪明钱为 0,
    # 此时**不能**写"聪明钱N人在买"(原文会出现"聪明钱0人在买"这种自相矛盾的标题)。
    n_smart = sum(int(r.get('smart') or 0) for r in picks)
    mcs = [float(r.get('market_cap') or 0) for r in picks if r.get('market_cap')]
    mc_s = f", 市值都 ≤{max(mcs)/1e6:.2f}M$" if mcs else ""
    if n_smart:
        head = f"我盯的 meme 池里\n聪明钱在买, 筹码还散着{mc_s}"
    else:
        head = f"我盯的 meme 池里\n只有这几个集中度低{mc_s}"

    def line(r, medal):
        c = r.get('cleaned_top10')
        c_s = f"{c*100:.0f}%" if c else "未知"
        mc = r.get('market_cap')
        mc_l = f"  市值{float(mc)/1e6:.2f}M$" if mc else ""
        sm = int(r.get('smart') or 0)
        sm_l = f"聪明钱{sm}人  " if sm else ""
        return (f"{medal} {r.get('symbol')} ({str(r.get('chain','?')).upper()})\n"
                f"{sm_l}集中度{c_s}{mc_l}")

    items = [line(r, ["🥇", "🥈", "🥉"][i]) for i, r in enumerate(picks)]
    tail = "我只列数据, 不喊单\n\n你先看哪个? 👇\n#MemeCoin #Crypto"
    main = ps.compose(head, items, tail, limit=MAX_TWEET_LEN)

    thread1 = ("我的筛选口径, 一共四条\n\n"
               "聪明钱/KOL/专业地址在买\n\n"
               "流动性≥30K\n\n"
               "集中度≤80%\n\n"
               "狙击手≥50% 或 DEV≥20% 的一律降级\n\n"
               "你最看重哪一条? 👇")
    if ps.xlen(thread1) > MAX_TWEET_LEN:      # 兜底, 正常不会触发
        thread1 = "为什么盯这几个?\n\n聪明钱在买 + 集中度低 + 狙击手/DEV 已剔除。"

    addrs = [f"{r.get('symbol')} ({r.get('chain','?')}) {r.get('address','')}"
             for r in sa[:5] if r.get('address')]
    # 地址逐条试, 保证整条 <= 上限(X 加权)
    tail2 = "\n\n链上数据筛选, 不代表必涨\n不构成投资建议。\n\n你会先查哪一个? 👇"
    picks_a = []
    for a in addrs:
        cand = "我自己会再核一遍\n合约地址(可自行查证):\n" + "\n".join(picks_a + [a]) + tail2
        if ps.xlen(cand) <= MAX_TWEET_LEN:
            picks_a.append(a)
    thread2 = ("我自己会再核一遍\n合约地址(可自行查证):\n" + "\n".join(picks_a) + tail2) if picks_a else \
              "链上数据筛选, 不代表必涨, 不构成投资建议。\n每天更新链上聪明钱动向。"

    chart = None
    try:
        import sys as _s2
        _s2.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_meme()
    except Exception as e:
        print('观察池出图失败:', str(e)[:80])
    return {"main": main, "thread": [thread1, thread2],
            "chart": str(chart) if chart else None,
            "label": "Meme观察池", "addresses": addrs,
            "symbols": [str(r.get('symbol') or '?') for r in picks]}


def compose_smart_live():
    """聪明钱扫货 —— 自建钱包池的最新链上买入。

    原在 alpha 的 X 草稿里, 现移到这里: 内容是 SOL/BSC 小币, 属 meme。
    数据源复用 square_publish.smart_live_picks()(只取买入, 方向由链上 Transfer 事件反推)。
    """
    import sys as _s
    _s.path.insert(0, '/opt/alpha-radar')
    try:
        import square_publish as _sp
    except Exception:
        return None
    picks = _sp.smart_live_picks()
    if not picks:
        return None
    head = "我盯的钱包池刚扫货\n全是 5000 万$ 市值以下的"
    items = [f"🟢 {x['tag']}  {x['mc']/1e6:.2f}M$\n{x['n']} 笔买入 {x['amt']:.3g} {x['qsym']}"
             for x in picks[:4]]
    tail = ("钱包池 = 币安排行榜筛出的实盘赢家\n"
            "胜率≥55% · 已实现盈亏≥1万$ · 交易数≥20\n\n"
            "我只列买入 —— 卖出是获利了结\n"
            "你盯过这个池子吗? 👇\n"
            "#Binance #MemeCoin")
    main = ps.compose(head, items, tail, limit=MAX_TWEET_LEN)
    thread = ("这个钱包池怎么来的?\n\n"
              "币安官方排行榜, 按三条硬指标筛:\n\n"
              "胜率≥55%\n\n"
              "已实现盈亏≥1万$\n\n"
              "交易数≥20\n\n"
              "筛完只留几十个地址, 盯他们最新的链上成交。\n\n"
              "只列买入 —— 卖出是获利了结。")
    chart = None
    try:
        import sys as _s2
        _s2.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_smart_live(picks)
    except Exception as e:
        print('扫货出图失败:', str(e)[:80])
    return {"main": main, "thread": [thread], "chart": str(chart) if chart else None,
            "label": "聪明钱扫货", "symbols": [str(x['sym']) for x in picks[:4]]}


def compose_smart_money():
    """聪明钱买入榜(X 版) —— 币安 trading-signal 的每笔聪明钱买入信号。

    原只发币安广场, 但实测**有效 cashtag 命中率仅 7%(8/111)** ——
    信号大多是未上市 DEX 小币, 挂不上 cashtag → 广场零收益。X 不需要 cashtag, 故移到这里。
    数据源复用 square_publish.smart_money_picks()。
    """
    import sys as _s
    _s.path.insert(0, '/opt/alpha-radar')
    try:
        import square_publish as _sp
    except Exception:
        return None
    d = _sp.smart_money_picks()
    if not d:
        return None
    fresh, done, cnt, gain = d['fresh'], d['done'], d['cnt'], d['gain']
    head = f"我盯的钱包池刚扫货\n{cnt(d['hook'])} 个地址同时买入同一个币"
    items, syms = [], []
    if fresh:
        block = ["🟢 刚触发(还没怎么涨)"]
        for r in fresh[:2]:
            block.append(f"{r.get('ticker')}  {cnt(r)} 人买入  触发后 {gain(r):+.0f}%")
            syms.append(str(r.get('ticker')))
        items.append('\n'.join(block))
    if done:
        block = ["🔥 已兑现(信号历史战绩)"]
        for r in done[:2]:
            block.append(f"{r.get('ticker')}  {cnt(r)} 人买入  触发后 {gain(r):+.0f}%")
            syms.append(str(r.get('ticker')))
        items.append('\n'.join(block))
    tail = ("聪明钱 = 链上被持续跟踪的专业地址\n"
            "他们买什么, 往往先于市场热度\n\n"
            "我不喊单, 只贴数据\n"
            "你会跟着看哪个? 👇\n"
            "#Binance #Crypto")
    main = ps.compose(head, items, tail, limit=MAX_TWEET_LEN)
    # 跟帖也带上具体数字 —— 自检会报"全帖无数字", 而且有数字的帖子确实更有说服力
    best = max([gain(r) for r in (fresh + done)] or [0])
    thread = ("我盯这两组的方式\n\n"
              "🟢 刚触发 = 信号刚出、还没怎么涨\n\n"
              f"最高 {max([gain(r) for r in fresh] or [0]):+.0f}%\n\n"
              "🔥 已兑现 = 信号出过之后的涨幅\n\n"
              f"最高 {max([gain(r) for r in done] or [0]):+.0f}%\n\n"
              f"我看到的最高一条: {best:+.0f}%\n\n"
              "人数 = 多少个专业地址同时买它\n\n"
              "你觉得哪组更值得看? 👇")
    chart = None
    try:
        import sys as _s2
        _s2.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_smart_money(d)
    except Exception as e:
        print('买入榜出图失败:', str(e)[:80])
    return {"main": main, "thread": [thread], "chart": str(chart) if chart else None,
            "label": "聪明钱买入榜", "symbols": syms}


LAST_POST = DATA / 'last_post.json'


def _last_symbols():
    try:
        return json.loads(LAST_POST.read_text(encoding='utf-8')).get('symbols') or []
    except Exception:
        return []


def _save_symbols(syms):
    try:
        LAST_POST.write_text(json.dumps({'symbols': syms, 'ts': time.time()}, ensure_ascii=False))
    except Exception:
        pass


def format_draft(d):
    """把草稿包排成可直接复制到 X 的 Telegram 文本(带字数)。"""
    parts = [f"📝 X发帖草稿(meme) · {d['label']}", ""]
    parts.append(f"【主帖 · {ps.xlen(d['main'])}/{MAX_TWEET_LEN}】")
    parts.append(d["main"])
    for i, t in enumerate(d.get("thread") or [], 1):
        parts += ["", f"【跟帖{i} · {ps.xlen(t)}/{MAX_TWEET_LEN}】", t]
    return "\n".join(parts)


def square_text(d):
    """给币安广场用的纯文本(不含地址)。"""
    return d["main"] + "\n\n" + "\n".join(d.get("thread") or [])


def post_square(text):
    """调 square_publish 的官方脚本发广场。"""
    import subprocess
    r = subprocess.run([AK_PY, SQUARE_POST, '--text', text],
                       capture_output=True, text=True, timeout=180)
    out = (r.stdout or '') + (r.stderr or '')
    return 'Success' in out, out.strip()[:200]


def tg_draft(text):
    """推 X 草稿到 meme 雷达自己的 Telegram bot(人工发 X)。

    原实现走 x_publisher.tg_send, 而它读的是 /etc/alpha-radar.env 的 bot
    → meme 的发帖原文从"币安/alpha"那个 bot 发出去了。改为用 MEME_TELEGRAM_*。
    (两个 bot 的 chat_id 目前相同, 区别在于发件 bot; 若要发到别的群,
     改 /etc/meme-radar.env 的 MEME_TELEGRAM_CHAT_ID 即可。)
    """
    import urllib.request
    token = secret(os.environ, 'MEME_TELEGRAM_BOT_TOKEN')
    chat = (secret(os.environ, 'MEME_DRAFT_CHAT_ID')
            or secret(os.environ, 'MEME_TELEGRAM_CHAT_ID'))
    if not token or not chat:
        print('TG 草稿失败: 未配置 MEME_TELEGRAM_BOT_TOKEN / MEME_TELEGRAM_CHAT_ID')
        return False
    body = json.dumps({'chat_id': chat, 'text': '📝 [Meme] X发帖草稿\n\n' + text,
                       'disable_web_page_preview': True}).encode()
    req = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage',
                                 data=body, headers={'Content-Type': 'application/json'})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if not r.get('ok'):
            print('TG 草稿失败:', str(r)[:120])
        return bool(r.get('ok'))
    except Exception as e:
        print('TG 草稿失败:', str(e)[:100])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', action='store_true', help='发 meme 观察池(默认: 仅 Telegram 带图草稿)')
    ap.add_argument('--square', action='store_true',
                    help='额外发币安广场(默认关闭: meme 多为未上币 DEX 代币, cashtag 无法归因、'
                         '且合约地址+推广措辞有账号受限风险)')
    args = ap.parse_args()

    if not args.scan:
        print(__doc__)
        return

    drafts = []
    for fn in (compose_scan, compose_smart_live, compose_smart_money):
        try:
            d = fn()
        except Exception as e:
            print(f'{fn.__name__} 失败: {type(e).__name__}: {str(e)[:80]}')
            d = None
        if d:
            drafts.append(d)
    if not drafts:
        print('无 meme 候选')
        return

    # 去重: 与上次选中的**完全相同**就跳过(一天发 3 次, 榜单没换就没必要重复推)
    syms = [x for d in drafts for x in (d.get('symbols') or [])]
    if syms and syms == _last_symbols():
        print(f'与上次完全相同({", ".join(syms)}), 跳过不发')
        return

    # 广场只发观察池那栏(聪明钱扫货有它自己的 square-smartlive 定时器, 免得重复发)
    scan = next((d for d in drafts if d.get('label') == 'Meme观察池'), None)
    if args.square and scan:
        ok, out = post_square(square_text(scan))
        print('广场:', '已发' if ok else f'失败 {out}')
    else:
        print('广场: 已跳过(默认不发; 需要时加 --square)')

    sent = False
    for d in drafts:
        text = format_draft(d)
        print(text)
        print('---')
        if ps.xlen(d['main']) > MAX_TWEET_LEN:
            print(f"⚠️ {d['label']} 主帖 {ps.xlen(d['main'])} 加权字, 超过 X 的 {MAX_TWEET_LEN} 上限")
        # 每栏各自带图。Telegram 图注有 1024 字上限, 所以一栏一条消息, 不合并。
        # 只审**主帖**(X 的 280 上限是按单条推文算的);
        # 审整份草稿(主帖+跟帖拼接)会永远超限 → 变成噪音告警, 等于没有
        for _w in ps.audit(d['main'], platform='x'):
            print(f'[风格告警·x · {d["label"]}] {_w}')
        for _i, _t in enumerate(d.get('thread') or [], 1):
            for _w in ps.audit(_t, platform='x'):
                print(f'[风格告警·x · {d["label"]} 跟帖{_i}] {_w}')
        chart = Path(d['chart']) if d.get('chart') else None
        if chart and chart.exists() and tg_send_photo(chart, text):
            print(f'X草稿: 已推 Telegram(带图 {chart.name} · {d["label"]})'); sent = True
        elif tg_draft(text):
            print(f'X草稿: 已推 Telegram(纯文字 · {d["label"]})'); sent = True
    if sent:
        _save_symbols(syms)      # 只有真的推出去才记, 失败下轮还能重试


if __name__ == '__main__':
    main()
