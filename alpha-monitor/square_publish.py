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
    """币安广场 cashtag: $SYMBOL(仅ASCII可识别符号加, 中文/特殊字符跳过)。
    Write-to-Earn 只有带 cashtag 的帖子才能把读者交易归因给作者。"""
    sym = str(symbol or '').strip()
    if not sym or not sym.isascii():
        return sym
    # 只保留字母数字(部分 symbol 含特殊字符如 KP⚡ 无法成 cashtag)
    clean = ''.join(ch for ch in sym if ch.isalnum())
    if not clean or not clean[0].isalpha():
        return sym
    return f"${clean}"


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
    lines = ['📊 币安Alpha妖币观察池 · 大户控盘筛选',
             f'{time.strftime("%m-%d", time.gmtime())} S级候选:']
    for x in sa[:3]:
        lines.append(f"• {cashtag(x['symbol'])} 清洗后集中度 {x['cleaned_top10']*100:.0f}% · 合约持仓 {x['oi_notional']/1e6:.1f}M$")
    lines.append('')
    lines.append('筛选逻辑: 已上币安合约 + 未上现货 + 剔除LP后大户集中度≥60%')
    lines.append('点击上方币种可查看实时行情与合约数据。')
    lines.append('现货上市前是资金布局窗口期。数据筛选, 不构成投资建议。')
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
    lines = ['📊 币安Alpha雷达复盘 · 持仓动向',
             f'{time.strftime("%m-%d", time.gmtime())}:']
    lines += ups[:3] + downs[:3]
    lines.append('')
    lines.append('持仓上升=资金布局, 持仓下降=资金撤退。数据筛选, 不构成投资建议。')
    text = '\n'.join(lines)
    imgs = [str(chart)] if chart and Path(chart).exists() else []
    return text, imgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', action='store_true')
    ap.add_argument('--review', action='store_true')
    ap.add_argument('--test', action='store_true')
    ap.add_argument('--text', type=str)
    args = ap.parse_args()

    if not api_key():
        print('缺少 BINANCE_SQUARE_OPENAPI_KEY(env 或 /etc/square-post.env)')
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
