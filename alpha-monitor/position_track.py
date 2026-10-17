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


REALERT_HOURS = 6   # 同一信号多久重提醒一次。止损只报一次、忽略了就没了 —— 那是真金白银的风险,
                    # 所以改成周期性重提醒; 6 小时是"不至于刷屏, 又不至于忘掉"的折中。


def _due(fired, key, now):
    """该信号是否到了可以(重)报的时间。首次触发(不在 fired) → True;
    已报过的, 距上次 >= REALERT_HOURS 才再报一次。"""
    last = fired.get(key)
    if last is None:
        return True
    return (now - last) >= REALERT_HOURS * 3600


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


def exchange_positions():
    """从币安读取**真实持仓**(只读接口)。读不到就返回空, 不影响手工登记的持仓。

    这样就不用再手工 --add: 你在交易所开的仓, 雷达自动盯止盈止损。
    """
    try:
        sys.path.insert(0, '/opt/radar-maintenance')
        import binance_account
        _, ps = binance_account.snapshot()
    except Exception as e:
        print(f'(交易所持仓读取失败, 仅用手工登记: {type(e).__name__})')
        return []
    out = []
    for x in ps:
        sym = str(x.get('symbol') or '').upper()
        if not sym:
            continue
        base = sym[:-4] if sym.endswith('USDT') else sym
        out.append({'symbol': base, 'futures': sym, 'entry': x.get('entry'),
                    'stop_pct': 0.15, 'trail_pct': 0.20,
                    'side': x.get('side') or 'LONG',
                    'amount': x.get('amount'), 'unrealized': x.get('unrealized'),
                    'source': 'exchange', 'created': time.time()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--add', nargs='+', help='--add SYMBOL ENTRY [STOP%%] [TRAIL%%]')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--run', action='store_true', help='检查并推送止盈止损')
    ap.add_argument('--sync', action='store_true', help='只看当前交易所真实持仓(只读)')
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

    if args.sync:
        ex = exchange_positions()
        if not ex:
            print('交易所无持仓(或读取失败)')
        for r in ex:
            print(f"  {r['side']:<5} {r['symbol']:<14} 成本 {r['entry']:<12.8g} 数量 {r.get('amount')}"
                  f"  盈亏 {r.get('unrealized')}")
        return

    if not args.run:
        print(__doc__)
        return

    # 合并: 手工登记的 + 交易所真实持仓(同一标的后者覆盖前者)
    ex = exchange_positions()
    if ex:
        manual = [r for r in rows if r.get('symbol', '').upper()
                  not in {e['symbol'] for e in ex}]
        merged = ex + manual
        if ex:
            print(f'交易所真实持仓 {len(ex)} 个' + (f' + 手工登记 {len(manual)} 个' if manual else ''))
        rows = merged

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
    now = time.time()
    for p in rows:
        sym = p.get('symbol', '?')
        futs = p.get('futures', sym + 'USDT')
        entry = num(p.get('entry'))
        stop_pct = num(p.get('stop_pct')) or 0.15
        trail_pct = num(p.get('trail_pct')) or 0.20
        price = price_of(api, futs)
        if not price or not entry:
            continue
        side = str(p.get('side') or 'LONG').upper()
        is_long = side != 'SHORT'
        prev = num(state.get(sym, {}).get('peak')) or entry
        # 顺势方向的"最优价": 多头看最高, 空头看最低
        peak = max(prev, price) if is_long else min(prev, price)
        st = state.get(sym, {})
        fired = st.get('fired', {})
        if isinstance(fired, list):        # 兼容旧格式(纯 list)
            fired = {k: time.time() for k in fired}
        # T3.2(2026-10-17): 止损线**锚定首次见到的成本 entry0**, 不随加仓漂移。
        # 加仓后仓位更大, 同样百分比回撤亏得更多 —— 止损线不能在这时放宽。
        entry0 = num(st.get('entry0')) or entry
        stop_line = entry0 * (1 - stop_pct) if is_long else entry0 * (1 + stop_pct)
        # P3: 成本变了要**显式提示**, 但明确告知止损线不再跟着移动。
        _prev_entry = num(st.get('entry'))
        if _prev_entry and abs(_prev_entry - entry) > max(1e-12, entry * 1e-6):
            alerts.append(f"⚠️ 成本价变动 · {sym}\n{_prev_entry:.6g} → {entry:.6g}(说明有加仓/减仓)。"
                          f"止损线**不随成本移动**, 仍锚定 {stop_line:.6g}"
                          f"(首次成本 {entry0:.6g} 的 -{stop_pct:.0%})。")
        tag = f'[{side}] ' if not is_long else ''
        # 1) 固定止损(锚点 = entry0, 不是加权成本)
        hit_stop = price <= stop_line if is_long else price >= stop_line
        if hit_stop and _due(fired, 'stop', now):
            alerts.append(f"🛑 止损触发 · {tag}{sym}\n现价 {price:.8g} 已{'跌破' if is_long else '升破'}止损线 "
                          f"{stop_line:.8g}(锚定首次成本 {entry0:.8g} 的 -{stop_pct:.0%}; "
                          f"当前加权成本 {entry:.8g})。")
            fired['stop'] = now
        # 2) 移动止盈(浮盈达 +30% 后启用)
        trail_on = peak >= entry * 1.3 if is_long else peak <= entry * 0.7
        trail_hit = (price <= peak * (1 - trail_pct)) if is_long else (price >= peak * (1 + trail_pct))
        if trail_on and trail_hit and _due(fired, 'trail', now):
            gained = (peak / entry - 1) if is_long else (1 - peak / entry)
            alerts.append(f"📉 移动止盈 · {tag}{sym}\n{'最高' if is_long else '最低'} {peak:.8g} 回落 -{trail_pct:.0%} "
                          f"至 {price:.8g}(成本 {entry:.8g}, 曾浮盈 {gained:+.0%})。")
            fired['trail'] = now
        # 3) 分批止盈
        for mult in (1.5, 2.0, 3.0):
            key = f'tp{mult}'
            hit = price >= entry * mult if is_long else price <= entry / mult
            if hit and _due(fired, key, now):
                alerts.append(f"💰 达到 {mult:g}x · {tag}{sym}\n现价 {price:.8g}(成本 {entry:.8g})。分批止盈观察点。")
                fired[key] = now
        state[sym] = {'peak': peak, 'fired': fired, 'last': price, 'entry': entry,
                      'entry0': entry0, 'side': side, 'checked': time.time()}
    # P4b: 清理已不在跟踪列表里的标的(原实现从不清理 —— BTW 平仓后状态仍残留)。
    _live = {str(p.get('symbol', '?')).upper() for p in rows}
    for _k in [k for k in state if str(k).upper() not in _live]:
        state.pop(_k, None)

    STATE.write_text(json.dumps(state, ensure_ascii=False))

    if alerts:
        body = '🎯 持仓止盈止损\n\n' + '\n\n'.join(alerts)
        print(body)
        tg_send(body)
    else:
        print(f'检查 {len(rows)} 个持仓, 无触发。')


if __name__ == '__main__':
    main()
