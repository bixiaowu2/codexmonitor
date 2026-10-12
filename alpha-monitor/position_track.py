#!/usr/bin/env python3
"""持仓止盈止损跟踪(复用 alpha-radar/positions.py 的成熟阈值)。

阈值(与 positions.py 一致):
  - 固定止损 15%(可自定义)
  - 浮盈达 +30% 后启用 20% 移动止盈(可自定义)
  - 分批止盈提示: 1.5x / 2x / 3x

登记方式(文件即数据源, 无需 Telegram 命令):
  /var/lib/demon-radar/positions.json:
    [{"symbol":"KOMA","futures":"KOMAUSDT","entry":0.0082,"stop_pct":0.15,"trail_pct":0.20,"created":1791...}]
  或用: position_track.py --add KOMA 0.0082 [止损%] [移动回撤%]

与 demon_track 的分工: demon_track 盯"候选"(买前信号); 本脚本盯"你的仓"(买后风控)。
"""
from __future__ import annotations
import json, sys, time, argparse
from pathlib import Path

sys.path.insert(0, '/opt/alpha-radar')
from radar import PublicAPI, FUTURES, num

DATA = Path('/var/lib/demon-radar')
POSITIONS = DATA / 'positions.json'
STATE = DATA / 'positions_state.json'
TG_SCRIPT = '/opt/alpha-radar/x_publisher.py'


def tg_send(text):
    """直发 Telegram(复用 x_publisher 的发送函数)。"""
    try:
        import x_publisher as xp
        return xp.tg_send(text)
    except Exception as e:
        print('TG 发送失败:', str(e)[:80])
        return False


def load_positions():
    try:
        return json.loads(POSITIONS.read_text(encoding='utf-8'))
    except Exception:
        return []


def save_positions(rows):
    DATA.mkdir(parents=True, exist_ok=True)
    POSITIONS.write_text(json.dumps(rows, ensure_ascii=False, indent=2))


def price_of(api, futures_sym):
    try:
        kl = api.get(FUTURES + '/fapi/v1/klines',
                     {'symbol': futures_sym, 'interval': '1h', 'limit': 2})
        if isinstance(kl, list) and kl:
            return num(kl[-1][4])
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--add', nargs='+', help='--add SYMBOL ENTRY [STOP%%] [TRAIL%%]')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--run', action='store_true', help='检查并推送止盈止损')
    args = ap.parse_args()

    rows = load_positions()

    if args.add:
        sym = args.add[0].upper()
        entry = num(args.add[1]) if len(args.add) > 1 else None
        stop = (num(args.add[2]) / 100) if len(args.add) > 2 else 0.15
        trail = (num(args.add[3]) / 100) if len(args.add) > 3 else 0.20
        if not entry or entry <= 0:
            print('用法: --add SYMBOL ENTRY [STOP%] [TRAIL%]')
            return
        rows = [r for r in rows if r.get('symbol', '').upper() != sym]
        rows.append({'symbol': sym, 'futures': sym + 'USDT', 'entry': entry,
                     'stop_pct': stop or 0.15, 'trail_pct': trail or 0.20,
                     'created': time.time()})
        save_positions(rows)
        print(f'已登记持仓: {sym} 成本 {entry} 止损 {stop:.0%} 移动 {trail:.0%}')
        return

    if args.list:
        if not rows:
            print('(未登记任何持仓)')
        for r in rows:
            print(f"  {r.get('symbol')} 成本 {r.get('entry')} 止损 {r.get('stop_pct',0):.0%} 移动 {r.get('trail_pct',0):.0%}")
        return

    if not args.run:
        print(__doc__)
        return

    if not rows:
        print('未登记持仓, 跳过')
        return

    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:
            state = {}
    api = PublicAPI('/var/lib/demon-radar/pos-cache', ttl=300)
    alerts = []
    for p in rows:
        sym = p.get('symbol', '?')
        futs = p.get('futures', sym + 'USDT')
        entry = num(p.get('entry'))
        stop_pct = num(p.get('stop_pct')) or 0.15
        trail_pct = num(p.get('trail_pct')) or 0.20
        price = price_of(api, futs)
        if not price or not entry:
            continue
        peak = max(num(state.get(sym, {}).get('peak')) or entry, price)
        fired = set(state.get(sym, {}).get('fired', []))
        # 1) 固定止损
        if price <= entry * (1 - stop_pct) and 'stop' not in fired:
            alerts.append(f"🛑 止损触发 · {sym}\n现价 {price:.8g} 已跌破止损线 {entry*(1-stop_pct):.8g}(成本 {entry:.8g} 的 -{stop_pct:.0%})。")
            fired.add('stop')
        # 2) 移动止盈(浮盈达 +30% 后启用)
        if peak >= entry * 1.3 and price <= peak * (1 - trail_pct) and 'trail' not in fired:
            alerts.append(f"📉 移动止盈 · {sym}\n最高 {peak:.8g} 回落 -{trail_pct:.0%} 至 {price:.8g}(成本 {entry:.8g}, 曾浮盈 {peak/entry-1:+.0%})。")
            fired.add('trail')
        # 3) 分批止盈
        for mult in (1.5, 2.0, 3.0):
            key = f'tp{mult}'
            if price >= entry * mult and key not in fired:
                alerts.append(f"💰 达到 {mult:g}x · {sym}\n现价 {price:.8g}(成本 {entry:.8g})。分批止盈观察点。")
                fired.add(key)
        state[sym] = {'peak': peak, 'fired': sorted(fired), 'last': price, 'checked': time.time()}
    STATE.write_text(json.dumps(state, ensure_ascii=False))

    if alerts:
        body = '🎯 持仓止盈止损\n\n' + '\n\n'.join(alerts)
        print(body)
        tg_send(body)
    else:
        print(f'检查 {len(rows)} 个持仓, 无触发。')


if __name__ == '__main__':
    main()
