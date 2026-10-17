#!/usr/bin/env python3
"""聪明钱异动提醒 —— 币安官方 address-tracker/trades(被跟踪钱包的链上成交)。

**做什么**: 拉取"被跟踪钱包"的最新成交, 把新的(按 txHash 去重)推到 Telegram。
  提醒内容: 哪个代币、市值多少、哪个钱包、成交金额(BNB)、时间。

**关于买卖方向**: 该接口**不暴露方向** —— `tradeType` 是数字码(实测有 3/4/5/6/8),
  文档未说明, `realizedPnlUsd` 恒为 null, 同一钱包同一币的时序也看不出买卖模式。
  所以本提醒**只报"异动", 不声称是买入还是卖出**。方向请自行在链上核对。

**数据源**: GET /api/v1/dex/market/address-tracker/trades
  参数: binanceChainId + trackerType(实测 '1' 可用) + page + size
  返回: {trades:[{txHash, walletAddress, tokenSymbol, tokenContractAddress,
                  tokenPrice, marketCap, quoteTokenSymbol, quoteTokenAmount,
                  tradeType, tradeTime}]}

**去重**: /var/lib/meme-radar/tracker_seen.json 记已推过的 txHash(保留最近 500 个)。
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

sys.path.insert(0, '/opt/alpha-radar')
import web3_client as w3

DATA = Path('/var/lib/meme-radar')
SEEN = DATA / 'tracker_seen.json'
TG = '/opt/alpha-radar/x_publisher.py'

CHAIN_NAME = {w3.CHAIN_BSC: 'BSC', w3.CHAIN_SOL: 'SOL'}
CHAIN_ARG = {w3.CHAIN_BSC: '56', w3.CHAIN_SOL: 'CT_501'}
TRACKER_TYPE = '1'

# tradeType → 买卖方向。**由链上数据反推得出**(/tmp/tt_verify.py):
#   取 tx receipt 解析 ERC-20 Transfer 事件 —— 钱包在 to 位置=买入, 在 from 位置=卖出。
#   实测 88 笔零混淆: 3=买(10/0) 5=买(12/0) | 4=卖(0/13) 6=卖(0/41) 8=卖(0/2)。
#   (3/5 与 4/6/8 的区别应是 DEX/成交类型, 对我们只需方向。)
TT_BUY = {'3', '5'}
TT_SELL = {'4', '6', '8'}

MAX_PUSH = 8          # 单次最多推几条, 防刷屏
MIN_QUOTE = 0.5       # 成交额下限(计价币, 如 BNB) —— 太小的忽略


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_seen():
    try:
        return json.loads(SEEN.read_text())
    except Exception:
        return []


def tg_send(text):
    """用 meme 自己的 bot 发(凭据由 systemd EnvironmentFile 注入 os.environ)。

    不用 x_publisher.tg_send —— 它读 /etc/alpha-radar.env(600 root), 而本服务跑在
    meme-radar 用户下, 读不到。
    """
    import os, urllib.request
    token = os.environ.get('MEME_TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.environ.get('MEME_TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        print('TG 失败: 未注入 MEME_TELEGRAM_*(需经 systemd EnvironmentFile 运行)')
        return False
    body = json.dumps({'chat_id': chat, 'text': text,
                       'disable_web_page_preview': True}).encode()
    req = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage',
                                 data=body, headers={'Content-Type': 'application/json'})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if not r.get('ok'):
            print('TG 失败:', str(r)[:120])
        return bool(r.get('ok'))
    except Exception as e:
        print('TG 失败:', str(e)[:100])
        return False


def main():
    seen = load_seen()
    seen_set = set(seen)
    fresh = []
    for cid in (w3.CHAIN_BSC, w3.CHAIN_SOL):
        try:
            d = w3.call('GET', '/api/v1/dex/market/address-tracker/trades',
                        {'binanceChainId': cid, 'trackerType': TRACKER_TYPE,
                         'page': 1, 'size': 50})
        except Exception as e:
            print(f'  {CHAIN_NAME[cid]} 拉取失败: {str(e)[:80]}')
            continue
        for t in (d or {}).get('trades') or []:
            h = str(t.get('txHash') or '')
            if not h or h in seen_set:
                continue
            amt = f(t.get('quoteTokenAmount')) or 0
            if amt < MIN_QUOTE:
                continue
            fresh.append((t, cid))
            seen_set.add(h)

    if not fresh:
        print('无异动')
        return

    # 按时间倒序取最近的
    fresh.sort(key=lambda x: -int(x[0].get('tradeTime') or 0))
    picked = fresh[:MAX_PUSH]
    _nb = sum(1 for t, _ in picked if str(t.get('tradeType')) in TT_BUY)
    _ns = sum(1 for t, _ in picked if str(t.get('tradeType')) in TT_SELL)
    lines = [f'🐋 聪明钱异动 · {len(fresh)} 笔 (买{_nb}/卖{_ns})', '']
    for t, cid in picked:
        mc = f(t.get('marketCap'))
        mc_s = f'市值{mc/1e6:.2f}M$' if mc else '市值?'
        amt = f(t.get('quoteTokenAmount')) or 0
        w = str(t.get('walletAddress') or '')[:10]
        tt = str(t.get('tradeType'))
        d = '🟢买入' if tt in TT_BUY else ('🔴卖出' if tt in TT_SELL else '⚪方向?')
        lines.append(f"• {d} {t.get('tokenSymbol')} ({CHAIN_NAME[cid]}) {mc_s} · "
                     f"{t.get('quoteTokenSymbol')} {amt:.3g} · 钱包 {w}…")
        if t.get('tokenContractAddress'):
            lines.append(f"  合约 {t['tokenContractAddress']}")
    if len(fresh) > MAX_PUSH:
        lines.append(f'(另有 {len(fresh) - MAX_PUSH} 笔未列出)')
    lines += ['', '被跟踪钱包 = 币安官方排行榜筛选(胜率≥55%/盈亏≥1万$)的地址。',
              '方向由链上 Transfer 事件反推(钱包在 to=买入 / 在 from=卖出)。数据筛选, 不构成投资建议。']
    body = '\n'.join(lines)
    print(body)
    if tg_send(body):
        print('telegram: sent')
    seen = (seen + [str(t.get('txHash')) for t, _ in fresh])[-500:]
    SEEN.write_text(json.dumps(seen))


if __name__ == '__main__':
    main()
