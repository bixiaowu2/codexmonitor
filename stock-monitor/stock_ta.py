#!/usr/bin/env python3
"""股票技术面启动确认(yfinance RSI + MA)。对 S 级候选算技术面, 出现启动迹象时推送。

启动确认信号(技术面):
  - RSI(14) 从超卖区(<40)向上突破 → 反弹启动;
  - 收盘价上穿 MA20 → 趋势启动;
  - RSI < 30(超卖)→ 关注反弹机会。
独立运行(读 stock_matches.json 的 S 级), TG+钉钉推送。
"""
from __future__ import annotations
import sys, json, time, types, os
from pathlib import Path
sys.path.insert(0, '/opt/stock-radar')
from notify import send_channel
sys.path.insert(0, '/opt/akshare-venv/lib/python3.12/site-packages')
import yfinance as yf

import sys as _sys
_sys.path.insert(0, '/opt/stock-radar')
from markets import market_of, MARKET_ORDER

DATA = Path('/var/lib/stock-radar')
MATCHES = DATA / 'stock_matches.json'
STATE = DATA / 'ta_state.json'


def secret(env, name):
    p = env.get(name + '_FILE')
    return Path(p).read_text().strip() if p else env.get(name, '').strip()


def make_cfg(env):
    return types.SimpleNamespace(
        telegram_enabled=env.get('STOCK_TELEGRAM_ENABLED', 'false').strip().lower() == 'true',
        telegram_token=secret(env, 'STOCK_TELEGRAM_BOT_TOKEN'),
        telegram_chat=secret(env, 'STOCK_TELEGRAM_CHAT_ID'),
        dingtalk_enabled=env.get('STOCK_DINGTALK_ENABLED', 'false').strip().lower() == 'true',
        dingtalk_webhook=secret(env, 'STOCK_DINGTALK_WEBHOOK'),
        dingtalk_secret=secret(env, 'STOCK_DINGTALK_SECRET'),
        dingtalk_keyword=env.get('STOCK_DINGTALK_KEYWORD', 'stock').strip(),
        timeout=10,
    )


def notify(env, body):
    cfg = make_cfg(env)
    if cfg.telegram_enabled:
        try:
            send_channel(cfg, body, 'telegram'); print('telegram: sent')
        except Exception as e:
            print('tg fail:', str(e)[:100])
    if cfg.dingtalk_enabled:
        try:
            send_channel(cfg, body, 'dingtalk'); print('dingtalk: sent')
        except Exception as e:
            print('dt fail:', str(e)[:100])


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0))
        losses.append(max(-ch, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def ta_snapshot(symbol):
    try:
        h = yf.Ticker(symbol).history(period='1mo', interval='1d')
        if h is None or len(h) < 25:
            return None
        closes = h['Close'].tolist()
        cur = closes[-1]
        ma20 = sum(closes[-20:]) / 20
        prev = closes[-2] if len(closes) >= 2 else cur
        r = rsi(closes)
        return {'rsi': r, 'ma20': ma20, 'cur': cur, 'prev': prev}
    except Exception:
        return None


def main():
    env = dict(os.environ)
    if not MATCHES.exists():
        print('无 S 级候选, 跳过')
        return
    try:
        matches = json.loads(MATCHES.read_text(encoding='utf-8'))
    except Exception:
        return
    s_tier = [m for m in matches if m.get('tier') == 'S']
    if not s_tier:
        print('无 S 级候选, 跳过')
        return

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    prev_rsi = state.get('rsi', {})
    alerted = set(state.get('alerted', []))

    ts = time.strftime('%m-%d %H:%M UTC', time.gmtime())
    alerts = {}          # 按市场分桶
    snapshots = {}
    for m in s_tier:
        sym = m.get('symbol', '')
        name = m.get('name', sym)
        snap = ta_snapshot(sym)
        if not snap:
            continue
        snapshots[sym] = snap['rsi']
        r, ma20, cur, prev = snap['rsi'], snap['ma20'], snap['cur'], snap['prev']
        if r is None:
            continue
        key = sym
        old_r = prev_rsi.get(sym)
        # 启动确认1: RSI 从超卖区(<40)上穿
        if old_r is not None and old_r < 40 <= r and key not in alerted:
            alerts.setdefault(market_of(sym), []).append(f"📊 技术面启动 · {name} ({sym})\nRSI(14) 从超卖区 {old_r:.0f} 回升至 {r:.0f}, 反弹动能增强。")
            alerted.add(key)
        # 启动确认2: 价格上穿 MA20
        if prev <= ma20 < cur and key + ':ma' not in alerted:
            alerts.setdefault(market_of(sym), []).append(f"📈 突破均线 · {name} ({sym})\n收盘价 {cur:.2f} 上穿 MA20 {ma20:.2f}, 趋势转强。")
            alerted.add(key + ':ma')
        # 关注: RSI 深度超卖
        if r < 30 and key + ':os' not in alerted:
            alerts.setdefault(market_of(sym), []).append(f"👀 超卖关注 · {name} ({sym})\nRSI(14) = {r:.0f} 深度超卖, 可能有反弹机会(风险高)。")
            alerted.add(key + ':os')

    state['rsi'] = snapshots
    state['alerted'] = list(alerted)
    state['last_run'] = time.time()
    STATE.write_text(json.dumps(state, ensure_ascii=False))

    if alerts:
        header = f"📐 股票技术面信号 @ {ts}\n(S级候选的 RSI/均线启动确认)\n"
        blocks = []
        for _mk in MARKET_ORDER:
            _a = alerts.get(_mk)
            if _a:
                blocks.append(f"━━━ {_mk} ({len(_a)}条) ━━━\n" + '\n\n'.join(_a))
        body = header + '\n\n'.join(blocks)
        print(body)
        notify(env, body)
    else:
        print(f'检查 {len(s_tier)} 个S级候选技术面, 无启动信号。')


if __name__ == '__main__':
    main()
