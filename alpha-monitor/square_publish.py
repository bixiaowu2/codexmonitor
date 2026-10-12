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


def post_text(text, title=None):
    args = ['--text', text]
    if title:
        args += ['--title', title]
    return run_script('post-text.mjs', args)


def post_image(text, images):
    return run_script('post-image.mjs', ['--text', text, '--images', ','.join(images)])


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


def compose_scan():
    """观察池: 文字 + 集中度/持仓图。"""
    try:
        m = json.loads((DATA / 'matches.json').read_text())
    except Exception:
        return None, []
    sa = [x for x in m if x.get('tier') in ('S', 'A')]
    if not sa:
        return None, []
    # 生成配图
    try:
        import sys
        sys.path.insert(0, '/opt/alpha-radar')
        import x_chart
        chart = x_chart.chart_scan()
    except Exception:
        chart = None
    lines = [f'🔍 我扒了全部币安Alpha代币, 只有 {len(sa[:3])} 个同时满足这三个条件',
             '',
             '① 已上币安合约  ② 还没上现货  ③ 剔除LP后大户集中度≥60%',
             '满足意味着: 主力已控盘, 现货上市前是资金布局窗口期。',
             '',
             f'{time.strftime("%m-%d", time.gmtime())} 观察池:']
    for x in sa[:3]:
        lines.append(f"• {cashtag(x['symbol'])} 清洗后集中度 {x['cleaned_top10']*100:.0f}% · 合约持仓 {x['oi_notional']/1e6:.1f}M$")
    lines.append('')
    lines.append('筛选逻辑: 已上币安合约 + 未上现货 + 剔除LP后大户集中度≥60%')
    lines.append('点击上方币种可查看实时行情与合约数据。')
    lines.append('数据筛选, 不构成投资建议。')
    text = '\n'.join(lines)
    imgs = [str(chart)] if chart and Path(chart).exists() else []
    return text, imgs


def compose_review():
    """复盘: 文字 + 持仓变化图。"""
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
        cur = t.get('oi_snap', {}).get(sym) or m.get('oi_notional') or 0
        base = m.get('oi_notional') or 0
        if base > 0:
            chg = (cur / base - 1) * 100
            if chg >= 5:
                ups.append(f"🔺 {cashtag(sym)} 持仓 {chg:+.0f}% ({base/1e6:.1f}M→{cur/1e6:.1f}M$)")
            elif chg <= -5:
                downs.append(f"🔻 {cashtag(sym)} 持仓 {chg:+.0f}% ({base/1e6:.1f}M→{cur/1e6:.1f}M$)")
    if not ups and not downs:
        return None, []
    _up = len(ups)
    _down = len(downs)
    if _up and not _down:
        _hook = f'📈 昨天 {_up} 个Alpha币被悄悄加仓 — 资金在往哪走?'
    elif _down and not _up:
        _hook = f'📉 昨天 {_down} 个Alpha币遭资金撤退 — 谁在被抛弃?'
    else:
        _hook = f'📊 Alpha雷达复盘: {_up} 个加仓 · {_down} 个撤退'
    lines = [_hook, '',
             f'{time.strftime("%m-%d", time.gmtime())} 合约持仓变化:']
    lines += (ups[:2] + downs[:1])[:3]  # cashtag 上限3
    lines.append('')
    lines.append('持仓上升=资金布局, 持仓下降=资金撤退。数据筛选, 不构成投资建议。')
    text = '\n'.join(lines)
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


def compose_signal(ev):
    """把 demon-track 的信号事件转成广场帖(带 cashtag + 走势图)。"""
    kind = ev.get('kind', '其他')
    sym = ev.get('symbol', '')
    futs = ev.get('futures', sym + 'USDT')
    title = SIGNAL_TITLE.get(kind, '📊 雷达信号')
    tag = cashtag(sym)
    raw = str(ev.get('text', ''))
    # 去掉内部标题行(第一行), 保留说明部分
    body = raw.split('\n', 1)[1].strip() if '\n' in raw else raw
    lines = [f'{title} · {tag}', body, '',
             '点击上方币种可查看实时行情。数据筛选, 不构成投资建议。']
    text = '\n'.join([x for x in lines if x is not None])
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


def compose_smart_money():
    """聪明钱买入榜。注意: 币安广场对单帖 cashtag 数量有服务端上限(实测>3会报220095),
    故全帖最多 3 个 cashtag: 刚触发 top2 + 已兑现 top1。"""
    try:
        import bn_skills
    except Exception:
        return None, []
    rows = []
    for chain, cid in (('bsc', bn_skills.CHAIN_BSC), ('solana', bn_skills.CHAIN_SOL)):
        try:
            for r in bn_skills.smart_money(chain_id=cid, page_size=100):
                rows.append(r)
        except Exception:
            continue
    buys = [r for r in rows if str(r.get('direction', '')).lower() == 'buy']
    if not buys:
        return None, []

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
    items = list(best.values())

    fresh, done = [], []
    for r in items:
        g = gain(r)
        if g is None:
            continue
        if g >= 25:
            done.append(r)
        elif g >= -10:
            fresh.append(r)
    fresh.sort(key=lambda r: -cnt(r))
    done.sort(key=lambda r: -(gain(r) or 0))

    lines = []
    hook = fresh[0] if fresh else (done[0] if done else (items[0] if items else None))
    if hook is not None:
        lines.append(f'🐋 聪明钱刚刚扫货: {cnt(hook)} 个地址同时买入同一个币')
        lines.append('')
    used = 0
    if fresh:
        lines.append('🟢 刚触发(还没怎么涨, 可重点关注):')
        for r in fresh[:2]:
            if used >= 3:
                break
            g = gain(r)
            lines.append(f'• {cashtag(r.get("ticker"))} · {cnt(r)} 人买入 触发后 {g:+.0f}%')
            used += 1
        lines.append('')
    if done and used < 3:
        lines.append('🔥 已兑现(信号历史战绩):')
        for r in done[:3 - used]:
            lines.append(f'• {cashtag(r.get("ticker"))} · {cnt(r)} 人买入 (触发后 {gain(r):+.0f}%)')
            used += 1
        lines.append('')
    lines += ['聪明钱=链上被持续跟踪的专业地址, 他们买什么往往先于市场热度。',
              '点击币种可查看实时行情。数据筛选, 不构成投资建议。']
    return '\n'.join(lines), []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', action='store_true')
    ap.add_argument('--review', action='store_true')
    ap.add_argument('--test', action='store_true')
    ap.add_argument('--signal', action='store_true', help='消费信号队列发广场')
    ap.add_argument('--smart-money', action='store_true', help='发聪明钱买入榜')
    ap.add_argument('--text', type=str)
    args = ap.parse_args()

    if not api_key():
        print('缺少 BINANCE_SQUARE_OPENAPI_KEY(env 或 /etc/square-post.env)')
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
