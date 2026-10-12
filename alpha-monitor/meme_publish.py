#!/usr/bin/env python3
"""Meme 币发帖器。

内容: meme 观察池(S/A级) + 聪明钱买入榜。
渠道: 币安广场(官方 API, 全自动) + X 草稿(推 Telegram 人工发, X额度恢复后可切全自动)。

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


def compose_scan():
    """meme 观察池帖。"""
    rows = load_matches()
    global _BINANCE_SYMS
    _BINANCE_SYMS = binance_symbols()
    sa = [r for r in rows if r.get('tier') in ('S', 'A')]
    sa.sort(key=lambda r: (0 if r.get('tier') == 'S' else 1, -(r.get('cleaned_top10') or 0), -(r.get('smart') or 0)))
    if not sa:
        return None
    top = sa[0]
    lines = [f'🔍 我从链上扒出 {len(sa)} 个"聪明钱正在买"的 meme 币', '',
             '筛选: 聪明钱/KOL/专业地址持仓 + 流动性充足 + 狙击手占比低', '']
    for r in sa[:3]:
        sym = tag_for(r.get('symbol'))
        ct = r.get('cleaned_top10')
        ct_s = f'集中度{ct*100:.0f}%' if ct else '集中度未知'
        smart = int(r.get('smart') or 0)
        lines.append(f'• {sym} ({r.get("chain","?")}) {ct_s} · 聪明钱{smart}人')
        if r.get('address'):
            lines.append(f'  合约 {r["address"]}')
    lines += ['', '聪明钱在买 = 专业地址提前布局, 不代表必涨。数据筛选, 不构成投资建议。']
    return '\n'.join(lines)


def post_square(text):
    """调 square_publish 的官方脚本发广场。"""
    import subprocess
    r = subprocess.run([AK_PY, SQUARE_POST, '--text', text],
                       capture_output=True, text=True, timeout=180)
    out = (r.stdout or '') + (r.stderr or '')
    return 'Success' in out, out.strip()[:200]


def tg_draft(text):
    """推 X 草稿到 Telegram(人工发 X)。复用 square_publish 的 tg_send。"""
    try:
        import x_publisher as xp
        return xp.tg_send('📝 X发帖草稿(meme)\n\n' + text)
    except Exception as e:
        print('TG 草稿失败:', str(e)[:80])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', action='store_true', help='发 meme 观察池(广场+X草稿)')
    ap.add_argument('--no-square', action='store_true', help='只出草稿不发广场')
    args = ap.parse_args()

    if not args.scan:
        print(__doc__)
        return

    text = compose_scan()
    if not text:
        print('无 meme 候选')
        return
    print(text)
    print('---')
    if not args.no_square:
        ok, out = post_square(text)
        print('广场:', '已发' if ok else f'失败 {out}')
    if tg_draft(text):
        print('X草稿: 已推 Telegram')


if __name__ == '__main__':
    main()
