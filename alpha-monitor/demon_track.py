#!/usr/bin/env python3
"""妖币候选跟踪器(只读)。只跟踪 S 级(清洗Top10≥60%)命中币。

信号:
  B. 启动: 资金费率转负(<-0.1%) / 合约持仓暴增(+30%) / 突破7日高点 → 推;
  A. 回撤: 从3日高点回撤≥25%(瀑布预警, 阈值放宽以拿住涨幅) → 推;
  T. 分批止盈: 较Alpha上线首收价 ≥3x / ≥10x → 推第一/第二档止盈;
  每天一次 S 级状态汇总。

较首收价的倍数用于止盈; 用首收价(而非当前成本)因为它是公开可复现的基准。
"""
from __future__ import annotations
import sys, json, time, os, concurrent.futures, types
from pathlib import Path
sys.path.insert(0, '/opt/alpha-radar')
import dingtalk
from radar import PublicAPI, FUTURES, num

DATA = Path('/var/lib/demon-radar')
MATCHES = DATA / 'matches.json'
STATE = DATA / 'track_state.json'
DRAWDOWN_PCT = 0.25
FR_NEG = -0.001
OI_SURGE = 1.30
TP1 = 3.0   # 第一档止盈倍数
TP2 = 10.0  # 第二档止盈倍数
PRICE_CACHE = DATA / 'price_cache.json'


def load_env():
    env = {}
    p = Path('/etc/alpha-radar.env')
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env


def secret(env, name):
    p = env.get(name + '_FILE')
    return Path(p).read_text().strip() if p else env.get(name, '').strip()


def telegram_send(token, chat, text):
    import urllib.request
    body = json.dumps({'chat_id': chat, 'text': text, 'disable_web_page_preview': True}).encode()
    req = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage',
                                 data=body, headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(req, timeout=15).read()


def dingtalk_send(env, text):
    if env.get('DEMON_DINGTALK_ENABLED', env.get('DINGTALK_ENABLED', 'false')).strip().lower() != 'true':
        return
    cfg = types.SimpleNamespace(
        ding_webhook=secret(env, 'DINGTALK_WEBHOOK'),
        ding_secret=secret(env, 'DINGTALK_SECRET'),
        ding_keyword=env.get('DINGTALK_KEYWORD', 'Alpha雷达').strip(),
    )
    if not cfg.ding_webhook:
        return
    dingtalk.send(cfg, text)


def fetch_price(api, futs):
    try:
        kl = api.get(FUTURES + '/fapi/v1/klines', {'symbol': futs, 'interval': '1d', 'limit': 200})
        if not isinstance(kl, list) or not kl:
            return None
        closes = [num(k[4]) for k in kl if isinstance(k, list) and len(k) >= 5 and num(k[4]) and num(k[4]) > 0]
        if not closes:
            return None
        # 3日高点改用小时线最高价: 原先用72个日收盘(实为72天高点), 盘中冲高回落检测严重滞后
        peak = max(closes[-3:])
        try:
            kh = api.get(FUTURES + '/fapi/v1/klines', {'symbol': futs, 'interval': '1h', 'limit': 72})
            if isinstance(kh, list) and kh:
                highs = [num(k[2]) for k in kh if isinstance(k, list) and len(k) >= 6 and num(k[2]) and num(k[2]) > 0]
                if highs:
                    peak = max(peak, max(highs))
        except Exception:
            pass
        return {'first': closes[0], 'last': closes[-1], 'peak_3d': peak}
    except Exception:
        return None


def fetch_oi(api, futs):
    try:
        oih = api.get(FUTURES + '/futures/data/openInterestHist', {'symbol': futs, 'period': '1d', 'limit': 2})
        if isinstance(oih, list) and oih:
            return num(oih[-1].get('sumOpenInterestValue'))
    except Exception:
        pass
    return None


def fetch_funding(api, futs):
    try:
        fr = api.get(FUTURES + '/fapi/v1/fundingRate', {'symbol': futs, 'limit': 5})
        if isinstance(fr, list) and fr:
            return [num(x.get('fundingRate')) for x in fr if isinstance(x, dict) and num(x.get('fundingRate')) is not None]
    except Exception:
        pass
    return []


def notify(env, token, chat, body):
    if token and chat:
        try:
            telegram_send(token, chat, body)
            print('telegram: sent')
        except Exception as e:
            print('telegram fail:', str(e)[:100])
    try:
        dingtalk_send(env, body)
        print('dingtalk: sent')
    except Exception as e:
        print('dingtalk fail:', str(e)[:100])


def main():
    env = load_env()
    token = secret(env, 'TELEGRAM_BOT_TOKEN')
    chat = secret(env, 'TELEGRAM_CHAT_ID')
    if not MATCHES.exists():
        print('无命中币, 跳过')
        return
    try:
        matches = json.loads(MATCHES.read_text(encoding='utf-8'))
    except Exception:
        print('命中文件损坏')
        return
    matches = [m for m in matches if isinstance(m, dict) and m.get('symbol')]
    # 只跟踪 S 级(高控盘)
    s_tier = [m for m in matches if m.get('tier') == 'S']
    if not s_tier:
        print('无 S 级命中币, 跳过')
        return

    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:
            state = {}
    drawn = set(state.get('drawn_symbols', []))
    fr_alerted = set(state.get('fr_alerted', []))
    oi_snap = state.get('oi_snap', {})
    tp_hit = set(state.get('tp_hit', []))

    api = PublicAPI('/var/lib/demon-radar/track-cache', ttl=300)
    alerts = []
    sq_events = []
    for m in s_tier:
        sym = m['symbol']
        futs = m.get('futures', sym + 'USDT')
        price = fetch_price(api, futs)
        if price:
            first, last, peak = price['first'], price['last'], price['peak_3d']
            # T. 分批止盈
            if first and first > 0:
                mult = last / first
                if mult >= TP2 and sym + ':tp2' not in tp_hit:
                    _msg = f"💰 第二档止盈 · {sym}\n较候选基准价(合约首日收盘) {first:.6g} 已涨 {mult:.1f}x (现价 {last:.6g})。\n建议再出 1/3 锁定利润, 剩 1/3 博更高。"
                    alerts.append(_msg)
                    sq_events.append({"kind": "止盈2", "symbol": sym, "futures": futs, "text": _msg, "ts": time.time()})
                    tp_hit.add(sym + ':tp2')
                elif mult >= TP1 and sym + ':tp1' not in tp_hit:
                    _msg = f"💰 第一档止盈 · {sym}\n较候选基准价(合约首日收盘) {first:.6g} 已涨 {mult:.1f}x (现价 {last:.6g})。\n建议出 1/3 锁定利润, 剩 2/3 继续持有。"
                    alerts.append(_msg)
                    sq_events.append({"kind": "止盈1", "symbol": sym, "futures": futs, "text": _msg, "ts": time.time()})
                    tp_hit.add(sym + ':tp1')
            # A. 回撤(瀑布预警, 25%)
            if peak and last:
                dd = (peak - last) / peak
                if dd >= DRAWDOWN_PCT:
                    if sym not in drawn:
                        _msg = f"⚠️ 回撤预警 · {sym}\n从3日高点 {peak:.6g} 回落 {dd*100:.1f}% 至 {last:.6g}。瀑布或已开始, 注意止损/止盈。"
                        alerts.append(_msg)
                        sq_events.append({"kind": "回撤", "symbol": sym, "futures": futs, "text": _msg, "ts": time.time()})
                        drawn.add(sym)
                else:
                    drawn.discard(sym)
        frs = fetch_funding(api, futs)
        if frs:
            cur = frs[-1]
            if cur is not None and cur < FR_NEG and sym not in fr_alerted:
                _msg = f"🚀 启动信号 · {sym}\n资金费率转负 {cur*100:.3f}%, 可能逼空拉升。"
                alerts.append(_msg)
                sq_events.append({"kind": "启动", "symbol": sym, "futures": futs, "text": _msg, "ts": time.time()})
                fr_alerted.add(sym)
        oi = fetch_oi(api, futs)
        prev = oi_snap.get(sym)
        if oi is not None:
            if prev and prev > 0 and oi > prev * OI_SURGE:
                _msg = f"📈 持仓异动 · {sym}\n合约持仓 {oi/1e6:.1f}M, 较前期 {prev/1e6:.1f}M 增 {oi/prev-1:+.1%}。"
                alerts.append(_msg)
                sq_events.append({"kind": "持仓", "symbol": sym, "futures": futs, "text": _msg, "ts": time.time()})
            oi_snap[sym] = oi

    state.update({'drawn_symbols': list(drawn), 'fr_alerted': list(fr_alerted), 'oi_snap': oi_snap, 'tp_hit': list(tp_hit), 'last_run': time.time()})
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    if sq_events:
        with (DATA / 'square_queue.jsonl').open('a', encoding='utf-8') as _qf:
            for _e in sq_events:
                _qf.write(json.dumps(_e, ensure_ascii=False) + '\n')

    if alerts:
        header = f"🎯 S级妖币跟踪 @ {time.strftime('%m-%d %H:%M UTC', time.gmtime())}\n当前S级: {', '.join(m['symbol'] for m in s_tier)}\n"
        body = header + '\n\n'.join(alerts)
        print(body)
        notify(env, token, chat, body)
    else:
        print(f'监控 {len(s_tier)} 个S级币, 无异动。')

    if time.time() - state.get('last_summary', 0) >= 24 * 3600:
        lines = [f'📊 S级妖币日更 @ {time.strftime("%m-%d %H:%M UTC", time.gmtime())}']
        for m in s_tier:
            sym = m['symbol']
            futs = m.get('futures', sym + 'USDT')
            price = fetch_price(api, futs)
            frs = fetch_funding(api, futs)
            fr = frs[-1] if frs else None
            oi = oi_snap.get(sym)
            px = price['last'] if price else None
            mult = (px / price['first']) if (price and price['first']) else None
            px_s = f'{px:.6g}' if px else '?'
            mult_s = f'{mult:.1f}x' if mult else '?'
            fr_s = f'{fr*100:.4f}%' if fr is not None else '?'
            oi_s = f'{oi/1e6:.1f}M' if oi else '?'
            lines.append(f"• {sym}: {px_s} ({mult_s}) 费率 {fr_s} 持仓 {oi_s}")
        notify(env, token, chat, '\n'.join(lines))
        state['last_summary'] = time.time()
        STATE.write_text(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
