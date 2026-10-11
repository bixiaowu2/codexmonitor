#!/usr/bin/env python3
"""妖币候选跟踪器(只读)。

对 demon_scan.py 筛选出的命中币, 实时监控:
  - B. 启动信号: 资金费率 / 合约持仓异动(负费率/持仓暴增)→ Telegram;
  - A. 回撤信号: 价格从近期高点回撤 >= 阈值 → 推"回撤预警";
  - 每天一次状态汇总(Telegram)。

从 /var/lib/demon-radar/matches.json 读取命中币。
"""
from __future__ import annotations
import sys, json, time, os, concurrent.futures
from pathlib import Path
sys.path.insert(0, '/opt/alpha-radar')
import types
import dingtalk
from radar import PublicAPI, FUTURES, num

DATA = Path('/var/lib/demon-radar')
MATCHES = DATA / 'matches.json'
STATE = DATA / 'track_state.json'
PRICE_CACHE = DATA / 'price_cache.json'
DRAWDOWN_PCT = 0.15  # 回撤阈值
FR_NEG_THRESHOLD = -0.001  # 负费率阈值(< -0.1%)
OI_SURGE_RATIO = 1.30  # 持仓暴增(较1天前+30%)
SUMMARY_SECONDS = 24 * 3600


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
    if env.get('DINGTALK_ENABLED', 'false').strip().lower() != 'true':
        return
    cfg = types.SimpleNamespace(
        ding_webhook=secret(env, 'DINGTALK_WEBHOOK'),
        ding_secret=secret(env, 'DINGTALK_SECRET'),
        ding_keyword=env.get('DINGTALK_KEYWORD', 'Alpha雷达').strip(),
    )
    if not cfg.ding_webhook:
        return
    dingtalk.send(cfg, text)


def fetch_price(api, futures_sym, alpha_id):
    """用合约1h klines 取最近价格序列(7天)。返回最近价、近期高点(3天)。返回空则跳过。"""
    try:
        kl = api.get(FUTURES + '/fapi/v1/klines', {'symbol': futures_sym, 'interval': '1h', 'limit': 168})
        if not isinstance(kl, list) or not kl:
            return None
        closes = sorted([num(k[4]) for k in kl if isinstance(k, list) and len(k) >= 6 and num(k[4]) and num(k[4]) > 0])
        if not closes:
            return None
        return {'last': closes[-1], 'peak_3d': max(closes[-72:]) if len(closes) >= 72 else max(closes)}
    except Exception:
        return None


def fetch_oi(api, futures_sym):
    try:
        oih = api.get(FUTURES + '/futures/data/openInterestHist',
                      {'symbol': futures_sym, 'period': '1d', 'limit': 2})
        if isinstance(oih, list) and len(oih) >= 1:
            return num(oih[-1].get('sumOpenInterestValue'))
    except Exception:
        pass
    return None


def fetch_funding(api, futures_sym):
    try:
        fr = api.get(FUTURES + '/fapi/v1/fundingRate', {'symbol': futures_sym, 'limit': 5})
        if isinstance(fr, list) and fr:
            return [num(x.get('fundingRate')) for x in fr if isinstance(x, dict) and num(x.get('fundingRate')) is not None]
    except Exception:
        pass
    return []


def main():
    env = load_env()
    token = secret(env, 'TELEGRAM_BOT_TOKEN')
    chat = secret(env, 'TELEGRAM_CHAT_ID')
    if not MATCHES.exists():
        print('无命中币, 跳过跟踪')
        return
    try:
        matches = json.loads(MATCHES.read_text(encoding='utf-8'))
    except Exception:
        print('命中文件损坏, 跳过')
        return
    matches = [m for m in matches if isinstance(m, dict) and m.get('symbol')]
    if not matches:
        print('无命中币, 跳过跟踪')
        return

    # 恢复状态(上次推送记录、OI 快照、回撤标志)
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:
            state = {}
    oi_snap = state.get('oi_snap', {})
    prev_drawn = set(state.get('drawn_symbols', []))
    alerted_fr = set(state.get('fr_alerted', []))

    api = PublicAPI('/var/lib/demon-radar/track-cache', ttl=600)
    alerts = []  # (kind, symbol, message)

    for m in matches:
        sym = m['symbol']
        futs = m.get('futures', sym + 'USDT')
        # 1) 回撤(A)
        price = fetch_price(api, futs, m.get('alpha_id', sym))
        if price:
            peak = price['peak_3d']
            last = price['last']
            dd = (peak - last) / peak if peak > 0 else 0
            if dd >= DRAWDOWN_PCT:
                if sym not in prev_drawn:
                    alerts.append(('A', sym, f"⚠️ 回撤预警 · {sym}\n从3日高点 {peak:.8g} 回落 {dd*100:.1f}% 至 {last:.8g} USDT。瀑布或刚开始, 注意止损。"))
                    prev_drawn.add(sym)
            else:
                if sym in prev_drawn:
                    prev_drawn.discard(sym)
        # 2) 资金费率(B)
        frs = fetch_funding(api, futs)
        if frs:
            cur = frs[-1]
            if cur is not None and cur < FR_NEG_THRESHOLD and sym not in alerted_fr:
                alerts.append(('B', sym, f"🚀 启动信号 · {sym}\n资金费率转负 {cur*100:.3f}%(空头给多头), 可能逼空拉升, 关注启动。"))
                alerted_fr.add(sym)
        # 3) 持仓异动(B)
        oi = fetch_oi(api, futs)
        prev = oi_snap.get(sym)
        if oi is not None:
            if prev and prev > 0 and oi > prev * OI_SURGE_RATIO:
                alerts.append(('B', sym, f"📈 持仓异动 · {sym}\n合约持仓名义 {oi/1e6:.1f}M USDT, 较前期 {prev/1e6:.1f}M 增 {oi/prev-1:+.1%}, 可能有资金进场。"))
            oi_snap[sym] = oi

    state.update({'oi_snap': oi_snap, 'drawn_symbols': list(prev_drawn), 'fr_alerted': list(alerted_fr), 'last_run': time.time()})
    STATE.write_text(json.dumps(state, ensure_ascii=False))

    if alerts:
        header = f'🎯 妖币命中跟踪 @ {time.strftime("%m-%d %H:%M UTC", time.gmtime())}\n当前命中: {", ".join(m["symbol"] for m in matches)}\n'
        body = header + '\n\n'.join(a[2] for a in alerts)
        if token and chat:
            try:
                telegram_send(token, chat, body)
                print('telegram: 跟踪警报已推')
            except Exception as e:
                print('telegram 失败:', str(e)[:120])
        try:
            dingtalk_send(env, body)
            print('dingtalk: 跟踪警报已推')
        except Exception as e:
            print('dingtalk 失败:', str(e)[:120])
        print(body)
    else:
        print(f'监控 {len(matches)} 个命中币, 无异动。')

    # 每日汇总(推送当前状态)
    if time.time() - state.get('last_summary', 0) >= SUMMARY_SECONDS:
        lines = [f'📊 妖币命中日更 @ {time.strftime("%m-%d %H:%M UTC", time.gmtime())}']
        for m in matches:
            sym = m['symbol']
            futs = m.get('futures', sym + 'USDT')
            oi = oi_snap.get(sym)
            frs = fetch_funding(api, futs)
            fr = frs[-1] if frs else None
            fr_s = f'{fr*100:.4f}%' if fr is not None else 'N/A'
            oi_s = f'{oi/1e6:.1f}M' if oi else 'N/A'
            lines.append(f"• {sym}  持仓 {oi_s}  费率 {fr_s}")
        sum_body = '\n'.join(lines)
        if token and chat:
            try:
                telegram_send(token, chat, sum_body)
                print('telegram: 日更已推')
            except Exception as e:
                print('telegram 汇总失败:', str(e)[:120])
        try:
            dingtalk_send(env, sum_body)
            print('dingtalk: 日更已推')
        except Exception as e:
            print('dingtalk 汇总失败:', str(e)[:120])
        state['last_summary'] = time.time()
        STATE.write_text(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
