#!/usr/bin/env python3
"""Meme 币候选跟踪器(只读)。对 meme-radar 的高分候选分级跟踪。

分级(按 score_meta.score):
  S级: score ≥ 40 且 safety_status == 'safe'  → 触发启动/回撤/止盈信号
  A级: score ≥ 25                            → 只排序不推信号
  B级: 其余                                  → 仅记录

信号:
  B. 启动: 1小时涨幅 ≥ +40%(急涨) → 推;
  A. 回撤: 从24小时高点回落 ≥ 25% → 推;
  T. 分批止盈: 较首次发现价 ≥3x / ≥10x → 推。
所有推送 TG + 钉钉。
"""
from __future__ import annotations
import sys, json, time, types
from pathlib import Path
sys.path.insert(0, '/opt/meme-radar')
from net import get_json
from notify import send_channel
from config import Config

DATA = Path('/var/lib/meme-radar')
STATE = DATA / 'track_state.json'
DRAWDOWN_PCT = 0.25
PUMP_1H = 0.40
TP1, TP2 = 3.0, 10.0


def secret(env, name):
    p = env.get(name + '_FILE')
    return Path(p).read_text().strip() if p else env.get(name, '').strip()


def tg_send(token, chat, text):
    cfg = make_cfg(dict(__import__('os').environ))
    send_channel(cfg, text, 'telegram')


def make_cfg(env):
    return types.SimpleNamespace(
        telegram_enabled=env.get('MEME_TELEGRAM_ENABLED', 'false').strip().lower() == 'true',
        telegram_token=secret(env, 'MEME_TELEGRAM_BOT_TOKEN'),
        telegram_chat=secret(env, 'MEME_TELEGRAM_CHAT_ID'),
        dingtalk_enabled=env.get('MEME_DINGTALK_ENABLED', 'false').strip().lower() == 'true',
        dingtalk_webhook=secret(env, 'MEME_DINGTALK_WEBHOOK'),
        dingtalk_secret=secret(env, 'MEME_DINGTALK_SECRET'),
        dingtalk_keyword=env.get('MEME_DINGTALK_KEYWORD', 'Meme雷达').strip(),
        timeout=10,
    )

def dt_send(env, text):
    cfg = make_cfg(env)
    if not cfg.dingtalk_enabled:
        return
    send_channel(cfg, text, 'dingtalk')


def notify(env, token, chat, body):
    sent = False
    if token and chat:
        try:
            tg_send(token, chat, body); print('telegram: sent'); sent = True
        except Exception as e:
            print('tg fail:', str(e)[:100])
    try:
        dt_send(env, body); print('dingtalk: sent'); sent = True
    except Exception as e:
        print('dt fail:', str(e)[:100])
    return sent


def get_matches():
    try:
        import sqlite3
        c = sqlite3.connect(str(DATA / 'meme-radar.sqlite'))
        r = c.execute("SELECT value FROM runtime WHERE key='latest'").fetchone()
        if not r:
            return []
        d = json.loads(r[0])
        return d.get('rows', [])
    except Exception:
        return []


def price_history(chain, address):
    """用 GeckoTerminal 池子日线。返回 {first,last,peak_24h} 或 None。"""
    try:
        net = {'bsc': 'bsc', 'solana': 'solana', 'robinhood': 'robinhood', 'xlayer': 'x-layer', 'arc': 'arc', 'stable': 'stable'}.get(chain)
        if not net:
            return None
        # 需要先拿 pair_address, 但 meme rows 里有。用 dexscreener 更简单(按 token)
        d = get_json(f'https://api.dexscreener.com/tokens/v1/{chain}/{address}', 10)
        if not isinstance(d, list) or not d:
            return None
        p = d[0]
        ch = (p.get('priceChange') or {})
        # dexscreener 只有 5m/1h/6h/24h 涨幅, 无历史价序列。这里返回当前 + 24h变化推算
        cur = float(p.get('priceUsd', 0))
        ch24 = float(ch.get('h24', 0)) if ch.get('h24') is not None else None
        if cur and cur > 0:
            first = cur / (1 + ch24 / 100) if ch24 is not None else cur
            return {'first': first, 'last': cur, 'chg24h': ch24}
    except Exception:
        pass
    return None


def main():
    import os
    # 变量由 systemd EnvironmentFile 注入(memetrack.service), 直接读 os.environ
    env = dict(os.environ)
    token = secret(env, 'MEME_TELEGRAM_BOT_TOKEN')
    chat = secret(env, 'MEME_TELEGRAM_CHAT_ID')

    matches = get_matches()
    if not matches:
        print('无 meme 候选, 跳过')
        return
    # 分级
    for m in matches:
        s = m.get('score_meta', {}).get('score', 0) if isinstance(m.get('score_meta'), dict) else 0
        m['_score'] = s
        m['_tier'] = 'S' if (s >= 40 and m.get('safety_status') == 'safe') else ('A' if s >= 25 else 'B')
    s_tier = [m for m in matches if m['_tier'] == 'S']
    sa = [m for m in matches if m['_tier'] in ('S', 'A')]

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    drawn = set(state.get('drawn', []))
    pump_alerted = set(state.get('pump_alerted', []))
    tp_hit = set(state.get('tp_hit', []))
    first_seen = state.get('first_seen', {})

    alerts = []
    ts = time.strftime('%m-%d %H:%M UTC', time.gmtime())
    for m in s_tier:
        sym = m.get('symbol', '?')
        chain = m.get('chain')
        addr = m.get('address')
        key = f'{chain}:{addr}'
        ph = price_history(chain, addr)
        cur_price = m.get('price_usd') or (ph['last'] if ph else None)
        if key not in first_seen and cur_price:
            first_seen[key] = cur_price
        # T 分批止盈
        base = first_seen.get(key)
        if base and cur_price and base > 0:
            mult = cur_price / base
            if mult >= TP2 and key + ':tp2' not in tp_hit:
                alerts.append(f"💰 第二档止盈 · {sym} ({chain})\n较发现价 {base:.6g} 已涨 {mult:.1f}x (现价 {cur_price:.6g})。建议再出 1/3。")
                tp_hit.add(key + ':tp2')
            elif mult >= TP1 and key + ':tp1' not in tp_hit:
                alerts.append(f"💰 第一档止盈 · {sym} ({chain})\n较发现价 {base:.6g} 已涨 {mult:.1f}x (现价 {cur_price:.6g})。建议出 1/3 锁利润。")
                tp_hit.add(key + ':tp1')
        # B 启动(急涨)
        chg24 = (ph or {}).get('chg24h')
        if chg24 is not None and chg24 >= PUMP_1H * 100 and key not in pump_alerted:
            alerts.append(f"🚀 启动信号 · {sym} ({chain})\n24h 急涨 +{chg24:.1f}%, 可能启动。")
            pump_alerted.add(key)
        # A 回撤(用 24h 高点近似: 若当前价较 24h 高点跌 25%+)
        # dexscreener 无高点序列, 用"24h涨幅从峰值大幅回落"近似: 若 1h 跌幅大
        # 简化: 有 priceChange.h1 大幅下跌
        try:
            d = get_json(f'https://api.dexscreener.com/tokens/v1/{chain}/{addr}', 8)
            ch1 = float((d[0].get('priceChange') or {}).get('h1', 0) or 0)
            if ch1 <= -15 and key not in drawn:
                alerts.append(f"⚠️ 回撤预警 · {sym} ({chain})\n1小时急跌 {ch1:.1f}%, 瀑布或已开始, 注意止损。")
                drawn.add(key)
            elif ch1 > -5:
                drawn.discard(key)
        except Exception:
            pass

    state.update({'drawn': list(drawn), 'pump_alerted': list(pump_alerted), 'tp_hit': list(tp_hit), 'first_seen': first_seen, 'last_run': time.time()})
    STATE.write_text(json.dumps(state, ensure_ascii=False))

    if alerts:
        header = f"🎯 Meme S级跟踪 @ {ts}\n当前S级: {', '.join(m.get('symbol','?') for m in s_tier)}\n"
        body = header + '\n\n'.join(alerts)
        print(body)
        notify(env, token, chat, body)
    else:
        print(f'监控 {len(s_tier)} 个Meme S级币, 无异动。')

    # 每天一次 S+A 排序
    if time.time() - state.get('last_summary', 0) >= 24 * 3600 and sa:
        lines = [f'📊 Meme候选日更 @ {ts}(S+A级)']
        for i, m in enumerate(sa, 1):
            s = m.get('_score', 0)
            px = m.get('price_usd')
            px_s = f'{px:.6g}' if px else '?'
            lines.append(f"{i}. [{m['_tier']}] {m.get('symbol','?')} ({m.get('chain','?')}) score {s:.0f} 价 {px_s}")
        notify(env, token, chat, '\n'.join(lines))
        state['last_summary'] = time.time()
        STATE.write_text(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
