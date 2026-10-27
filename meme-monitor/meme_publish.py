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
    # 变体池：按当天形状（几个标的 / 集中度差 / 有无聪明钱）选结构，并排除上次用过的
    import post_variants as _pv
    _rows = [{'symbol': r.get('symbol'), 'chain': r.get('chain'),
              'conc': r.get('cleaned_top10'), 'mc': r.get('market_cap'),
              'smart': int(r.get('smart') or 0)} for r in picks]
    _vid, head, items, tail = _pv.meme_pool(_rows)
    print(f"  [变体] Meme观察池 -> V{_vid} "
          f"(n={len(_rows)}, 聪明钱{sum(x['smart'] for x in _rows)}人)")
    main = ps.compose(head, items, tail, limit=MAX_TWEET_LEN)

    _tvid, thread1 = _pv.filter_thread(
        [('聪明钱/KOL/专业地址在买', '有人真金白银进场'),
         ('流动性≥30K', '低于这个数进出都难'),
         ('集中度≤80%', '筹码太集中容易被砸'),
         ('狙击手≥50% 或 DEV≥20% 一律降级', '这两类地址手里筹码太多')],
        column='meme_filter_thread')
    print(f"  [变体] Meme观察池跟帖 -> T{_tvid}")
    if ps.xlen(thread1) > MAX_TWEET_LEN:      # 兜底, 正常不会触发
        thread1 = "为什么盯这几个?\n\n聪明钱在买 + 集中度低 + 狙击手/DEV 已剔除。"

    # 合约地址跟帖。**S 级必须全带上**(用户 2026-10-21 明确要求):
    # 原来写 sa[:5], S 超过 5 个时后面的 S 会被截掉; address_thread 又是从尾部
    # 丢条的 —— 两个截断叠加, S 级的地址可能根本发不出去。这里显式保证:
    # 所有 S 排最前(全部带上), 再用 A 级补到 5 条。
    _s_rows = [r for r in sa if r.get('tier') == 'S' and r.get('address')]
    _a_rows = [r for r in sa if r.get('tier') != 'S' and r.get('address')]
    _addr_rows = _s_rows + _a_rows[:max(0, 5 - len(_s_rows))]
    addrs = [f"{r.get('symbol')} ({r.get('chain','?')}) {r.get('address','')}"
             for r in _addr_rows]
    # 地址逐条试, 保证整条 <= 上限(X 加权)
    tail2 = "\n\n链上数据筛选, 不代表必涨\n不构成投资建议。\n\n你会先查哪一个? 👇"
    # 一条跟帖只放得下 3 个地址(SOL 地址 44 字符 = 44 加权字, 280 上限)。
    # S 级超过 3 个时不能再截 —— 用户要求 S 的合约地址必须带上, 所以续到跟帖3/4。
    _threads2, _rest = [], addrs
    while _rest and len(_threads2) < 3:
        _avid, _t = _pv.address_thread(_rest, ps.xlen, MAX_TWEET_LEN,
                                       tail2 if not _threads2 else "\n\n不构成投资建议。")
        _threads2.append(_t)
        _used = sum(1 for a in _rest if a in _t)
        if not _used:                    # 一条都放不下 → 防死循环
            break
        _rest = _rest[_used:]
    thread2 = _threads2[0] if _threads2 else ""
    print(f"  [变体] Meme地址跟帖 -> T{_avid} ({len(_threads2)} 条, {len(addrs)} 个合约)")
    # S 级地址一条都不许丢 —— 这是用户点名的要求, 丢了必须喊出来
    _all_t2 = "\n".join(_threads2)
    _s_miss = [r.get('symbol') for r in _s_rows if str(r.get('address')) not in _all_t2]
    if _s_miss:
        print(f"  ⚠️ S 级地址没进跟帖(超出 {MAX_TWEET_LEN} 加权字): {', '.join(_s_miss)}")
    _miss = _pv.addr_covered(main, [{'sym': r.get('symbol')} for r in picks], addrs)
    if _miss:
        print(f"  ⚠️ 主帖点名但地址跟帖没覆盖: {', '.join(_miss)}")

    chart = None
    try:
        import sys as _s2
        _s2.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_meme()
    except Exception as e:
        print('观察池出图失败:', str(e)[:80])
    return {"main": main, "thread": [thread1] + _threads2,
            "chart": str(chart) if chart else None,
            "label": "Meme观察池", "addresses": addrs,
            "vid": _vid, "thread_vid": _tvid,
            "symbols": [str(r.get('symbol') or '?') for r in picks]}


def compose_smart_live():
    """链上追踪器扫货 —— 币安官方 address-tracker 的最新买入成交。

    原在 alpha 的 X 草稿里, 现移到这里: 内容是 SOL/BSC 小币, 属 meme。
    数据源复用 square_publish.smart_live_picks()(只取买入, 方向由链上 Transfer 事件反推)。

    口径(2026-10-21 更正): 这是**币安自己维护的追踪器**(trackerType=1), 不是自建钱包池。
    实测 SOL 侧与我们那 90 个地址的池子重合 0/32, BSC 侧 5/27 —— 文案不能再说「我盯的钱包池」。
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
    import post_variants as _pv
    _vid, head, items, tail = _pv.smart_live(picks)
    print(f"  [变体] 链上追踪扫货 -> V{_vid} (n={len(picks)})")
    main = ps.compose(head, items, tail, limit=MAX_TWEET_LEN)
    _tvid, thread = _pv.filter_thread(
        [('只看买入', '卖出是获利了结, 买入才是新动作'),
         ('方向反推自链上转账', '钱包在 to=买入 / 在 from=卖出'),
         ('同名币必须核合约', '本地表里 Heyi 有 33 个合约、Kimchi 有 233 个')],
        column='wallet_filter_thread')
    print(f"  [变体] 链上追踪扫货跟帖 -> T{_tvid}")
    # 合约地址跟帖 —— 同名不同币太常见(本地 pairs 表里 Heyi 有 33 个合约、Kimchi 有 233 个),
    # 不写地址读者搜 ticker 大概率买到另一个币。地址原样大小写, SOL 是 base58 不能小写。
    # focus_first: V2 会点名的那个币排最前 —— 它按钱包数选, 未必在成交额前几名,
    # 不提前就会出现「主帖点名、跟帖里没有它」。
    addrs = _pv.smart_live_addrs(picks, focus_first=True)
    tail2 = "\n\n链上数据, 不代表必涨\n不构成投资建议。"
    _avid, thread2 = _pv.address_thread(addrs, ps.xlen, MAX_TWEET_LEN, tail2)
    print(f"  [变体] 聪明钱扫货地址 -> T{_avid} ({len(addrs)} 个)")
    _miss = _pv.addr_covered(main, picks, addrs)
    if _miss:
        print(f"  ⚠️ 主帖点名但地址跟帖没覆盖: {', '.join(_miss)}")
    chart = None
    try:
        import sys as _s2
        _s2.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_smart_live(picks)
    except Exception as e:
        print('扫货出图失败:', str(e)[:80])
    return {"main": main, "thread": [thread, thread2], "chart": str(chart) if chart else None,
            "label": "聪明钱扫货", "addresses": addrs,
            "vid": _vid, "thread_vid": _tvid,
            "symbols": [str(x['sym']) for x in picks[:4]]}


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
    syms = [str(r.get('ticker')) for r in (fresh[:2] + done[:2])]
    # 变体池：按当天信号形状（刚触发/已兑现/两组都有）选结构，并排除上次用过的
    import post_variants as _pv
    _vid, head, items, tail = _pv.smart_money(fresh, done, cnt, gain)
    print(f"  [变体] 聪明钱买入榜 -> V{_vid} (刚触发{len(fresh)} 已兑现{len(done)})")
    main = ps.compose(head, items, tail, limit=MAX_TWEET_LEN)
    _tvid, thread = _pv.money_thread(fresh, done, cnt, gain)
    print(f"  [变体] 聪明钱买入榜跟帖 -> T{_tvid}")
    # 合约地址跟帖 —— trading-signal 每条都带 contractAddress, 同名币太多,
    # 不写地址读者只会搜到同名的另一个币。地址原样, 不小写(SOL base58)。
    # 顺序 = 重要性(address_thread 从尾部落): fresh[0] 是 V2 点名那个、
    # done[0] 是 V3 点名那个, 两者都不许因为放不下而被截掉。
    _rows = [fresh[0]] if fresh else []
    if done:
        _rows.append(done[0])
    _rows += fresh[1:3] + done[1:2]
    addrs = []
    _seen_sym = set()
    for r in _rows:
        ca = str(r.get('contractAddress') or '').strip()
        tk = str(r.get('ticker') or '').strip()
        if not ca or tk.lower() in _seen_sym:
            continue
        _seen_sym.add(tk.lower())
        addrs.append(f"{tk or '?'} ({str(r.get('chainId')) == '56' and 'BSC' or 'SOL'}) {ca}")
    tail2 = "\n\n数据筛选, 不代表必涨\n不构成投资建议。"
    _avid, thread2 = _pv.address_thread(addrs, ps.xlen, MAX_TWEET_LEN, tail2)
    print(f"  [变体] 聪明钱买入榜地址 -> T{_avid} ({len(addrs)} 个)")
    _miss = _pv.addr_covered(main, [r for r in _rows], addrs)
    if _miss:
        print(f"  ⚠️ 主帖点名但地址跟帖没覆盖: {', '.join(_miss)}")
    chart = None
    try:
        import sys as _s2
        _s2.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_smart_money(d)
    except Exception as e:
        print('买入榜出图失败:', str(e)[:80])
    return {"main": main, "thread": [thread, thread2], "chart": str(chart) if chart else None,
            "label": "聪明钱买入榜", "addresses": addrs, "symbols": syms,
            "vid": _vid, "thread_vid": _tvid}


LAST_POST = DATA / 'last_post.json'
# X 草稿台账 —— 与 x_publisher 共用一份, 好让「已发帖↔变体号」能对上。
DRAFT_LOG = Path('/var/lib/x-publisher/drafts.jsonl')


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


def _draft_log(d):
    """把推出去的 X 草稿落盘(与 x_publisher.drafts.jsonl 同一份台账)。

    原来 meme 线只推 Telegram、不落盘 —— 于是「哪条帖用了哪一版变体」无从追溯
    (post_variants._TAGS 只有 5 个标签对 7 个变体, 看帖文认不出)。补这条日志后,
    已发帖的正文可与 main 比对还原变体号。写失败不能影响出稿。
    """
    try:
        DRAFT_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {'ts': time.time(),
               'ts_human': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()),
               'label': d.get('label'), 'vid': d.get('vid'),
               'thread_vid': d.get('thread_vid'), 'chart': d.get('chart'),
               'main': d.get('main'), 'thread': d.get('thread') or [],
               'src': 'meme_publish'}
        with DRAFT_LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f'  [草稿日志] 写失败(不影响出稿): {type(e).__name__}')


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
        if not ok:
            try:
                sys.path.insert(0, '/opt/content-push')
                import relay as _relay
                _relay.send(f'⚠️ [广场] Meme 发布失败\n\n{out[:700]}\n\n'
                            '这条**没发出去**, 需要手动补发或重跑。')
            except Exception as e:
                print('  失败告警发不出去:', str(e)[:80])
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
        _ok = False
        if chart and chart.exists() and tg_send_photo(chart, text):
            print(f'X草稿: 已推 Telegram(带图 {chart.name} · {d["label"]})'); sent = True; _ok = True
        elif tg_draft(text):
            print(f'X草稿: 已推 Telegram(纯文字 · {d["label"]})'); sent = True; _ok = True
        if _ok:
            _draft_log(d)
    if sent:
        _save_symbols(syms)      # 只有真的推出去才记, 失败下轮还能重试


if __name__ == '__main__':
    main()
