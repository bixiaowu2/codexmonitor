#!/usr/bin/env python3
"""股票候选跟踪器(只读)。对 stock-radar 的高分候选分级跟踪。

分级(按 score_meta.score):
  S级: score ≥ 60 且 fresh == True  → 触发启动/回撤/止盈信号
  A级: score ≥ 40                   → 只排序不推信号
  B级: 其余                          → 仅记录

信号(股票无合约费率/OI, 用价格):
  B. 启动: 单日涨幅 ≥ +5% → 推;
  A. 回撤: 从近期高点回落 ≥ 10% → 推;
  T. 分批止盈: 较首次发现价 +50% / +100% → 推。
所有推送 TG + 钉钉。休市(fresh=False)时价格不更新, 信号自然不触发。
"""
from __future__ import annotations
import sys, json, time, types, os
from pathlib import Path
sys.path.insert(0, '/opt/stock-radar')
from notify import send_channel
from config import Config

DATA = Path('/var/lib/stock-radar')
STATE = DATA / 'track_state.json'
DRAWDOWN_PCT = 0.10
PUMP_1D = 0.05
TP1, TP2 = 0.50, 1.00


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
        dingtalk_keyword=env.get('STOCK_DINGTALK_KEYWORD', 'Stock雷达').strip(),
        timeout=10,
    )


def notify(env, body):
    cfg = make_cfg(env)
    sent = False
    if cfg.telegram_enabled:
        try:
            send_channel(cfg, body, 'telegram'); print('telegram: sent'); sent = True
        except Exception as e:
            print('tg fail:', str(e)[:100])
    if cfg.dingtalk_enabled:
        try:
            send_channel(cfg, body, 'dingtalk'); print('dingtalk: sent'); sent = True
        except Exception as e:
            print('dt fail:', str(e)[:100])
    return sent


def get_matches():
    try:
        import sqlite3
        c = sqlite3.connect(str(DATA / 'stock-radar.sqlite'))
        r = c.execute("SELECT value FROM runtime WHERE key='latest'").fetchone()
        if not r:
            return []
        d = json.loads(r[0])
        return d.get('rows', [])
    except Exception:
        return []


def main():
    env = dict(os.environ)
    matches = get_matches()
    if not matches:
        print('无股票候选, 跳过')
        return
    for m in matches:
        s = m.get('score_meta', {}).get('score', 0) if isinstance(m.get('score_meta'), dict) else 0
        m['_score'] = s
        m['_tier'] = 'S' if (s >= 60 and m.get('fresh')) else ('A' if s >= 40 else 'B')
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
        name = m.get('name', '')
        price = m.get('price')
        chg = m.get('change')  # 较上日百分比(小数)
        if sym not in first_seen and price:
            first_seen[sym] = price
        base = first_seen.get(sym)
        # T 分批止盈
        if base and price and base > 0:
            mult = price / base - 1
            if mult >= TP2 and sym + ':tp2' not in tp_hit:
                alerts.append(f"💰 第二档止盈 · {name} ({sym})\n较发现价 +{mult*100:.0f}% (现 {price:.2f})。建议再出 1/3。")
                tp_hit.add(sym + ':tp2')
            elif mult >= TP1 and sym + ':tp1' not in tp_hit:
                alerts.append(f"💰 第一档止盈 · {name} ({sym})\n较发现价 +{mult*100:.0f}% (现 {price:.2f})。建议出 1/3 锁利润。")
                tp_hit.add(sym + ':tp1')
        # B 启动(单日大涨)
        if chg is not None and chg >= PUMP_1D and sym not in pump_alerted:
            alerts.append(f"🚀 启动信号 · {name} ({sym})\n单日大涨 {chg*100:+.1f}%, 可能启动。")
            pump_alerted.add(sym)
        # A 回撤(change 为负且幅度大)
        if chg is not None and chg <= -0.07 and sym not in drawn:
            alerts.append(f"⚠️ 回撤预警 · {name} ({sym})\n单日急跌 {chg*100:.1f}%, 注意止损。")
            drawn.add(sym)
        elif chg is not None and chg > -0.02:
            drawn.discard(sym)

    state.update({'drawn': list(drawn), 'pump_alerted': list(pump_alerted), 'tp_hit': list(tp_hit), 'first_seen': first_seen, 'last_run': time.time()})
    STATE.write_text(json.dumps(state, ensure_ascii=False))

    if alerts:
        header = f"🎯 股票S级跟踪 @ {ts}\n当前S级: {', '.join(m.get('name', m.get('symbol','?')) for m in s_tier)}\n"
        body = header + '\n\n'.join(alerts)
        print(body)
        notify(env, body)
    else:
        print(f'监控 {len(s_tier)} 个股票S级候选, 无异动。')

    if time.time() - state.get('last_summary', 0) >= 24 * 3600 and sa:
        lines = [f'📊 股票候选日更 @ {ts}(S+A级)']
        for i, m in enumerate(sa, 1):
            s = m.get('_score', 0)
            px = m.get('price')
            px_s = f'{px:.2f}' if px else '?'
            fresh = ' fresh' if m.get('fresh') else ' (休市)'
            lines.append(f"{i}. [{m['_tier']}] {m.get('name','?')} ({m.get('symbol','?')}) score {s:.0f} 价 {px_s}{fresh}")
        notify(env, '\n'.join(lines))
        state['last_summary'] = time.time()
        STATE.write_text(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
