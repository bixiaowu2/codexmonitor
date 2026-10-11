#!/usr/bin/env python3
"""妖币候选扫描(只读)。

条件: Alpha + 合约 + 未上现货, 合约持仓名义 ≥ 阈值, 清洗后 Top10 ≥ 阈值。
清洗后 Top10 = 剔除 is_contract=1(合约/LP 地址)后的前 10 持仓占比。
两阶段: 先用 Binance 接口筛持仓, 再对候选串行查 GoPlus(避免限流)。

通知:
  - 命中集合变化(新进/退出) → Telegram + 钉钉 各推一条;
  - 无变化 → 静默, 不推送;
  - 若超过 24 小时无任何推送 → 仅 Telegram 发一条"今日无候选"轻提醒(不碰钉钉, 避免限流)。
"""
from __future__ import annotations
import sys, json, time, os, concurrent.futures, types
from pathlib import Path
sys.path.insert(0, '/opt/alpha-radar')
from radar import PublicAPI, TOKENS, FUTURES, universe, num
from binance_official import token_list
import dingtalk

DATA = Path('/var/lib/demon-radar')
SNAPSHOT = DATA / 'matches.json'
STATE = DATA / 'notify_state.json'
GOPLUS_DELAY = 0.6
HEARTBEAT_SECONDS = 24 * 3600


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


def cleaned_top10(raw):
    holders = raw.get('holders') or []
    vals = []
    for h in holders:
        if not isinstance(h, dict):
            continue
        v = num(h.get('percent'))
        if v is None:
            continue
        if str(h.get('is_contract')) in ('1', 'true'):
            continue
        # 数据合理性: 单个 holder 占比不应超过 100%(GoPlus 偶发脏数据)。
        if v > 1.0:
            return None
        vals.append(v)
    s = sum(sorted(vals, reverse=True)[:10])
    if s > 1.0:
        return None
    return s


def goplus(api, addr, cache_dir):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / (str(addr).lower() + '.json')
    cached_raw = None
    if f.exists():
        try:
            c = json.loads(f.read_text(encoding='utf-8'))
            cached_raw = c.get('raw')
            # 持币集中度变化慢, 缓存 6 小时, 减少 GoPlus 偶发空响应导致的抖动。
            if time.time() - c.get('ts', 0) < 21600:
                return cached_raw
        except Exception:
            pass
    raw = {}
    for _ in range(2):
        try:
            r = api.get('https://api.gopluslabs.io/api/v1/token_security/56',
                        {'contract_addresses': addr})
            raw = r.get('result', {}).get(str(addr).lower()) or r.get('result', {}).get(addr) or {}
            if isinstance(raw, dict) and raw:
                break
        except Exception:
            raw = {}
        time.sleep(1.5)
    if isinstance(raw, dict) and raw:
        f.write_text(json.dumps({'ts': time.time(), 'raw': raw}))
        return raw
    # 空响应回退到旧缓存(即使过期), 避免一次空结果抹掉有效数据。
    if cached_raw:
        return cached_raw
    return {}


def main():
    env = load_env()
    min_oi = num(env.get('DEMON_MIN_OI_USDT', '5000000')) or 5000000
    min_top10 = num(env.get('DEMON_MIN_CLEANED_TOP10', '0.40')) or 0.40
    token = secret(env, 'TELEGRAM_BOT_TOKEN')
    chat = secret(env, 'TELEGRAM_CHAT_ID')

    api = PublicAPI('/var/lib/demon-radar/cache', ttl=300)
    tokens, _ = token_list(api.data(TOKENS, {}))
    fx = api.get(FUTURES + '/fapi/v1/exchangeInfo')
    pairs, _ = universe(tokens, fx)
    spot = api.get('https://api.binance.com/api/v3/exchangeInfo')
    spot_bases = {s.get('baseAsset') for s in spot.get('symbols', []) if s.get('status') == 'TRADING'}

    def phase1(p):
        t, f = p['token'], p['future']
        sym = f['symbol']
        if f.get('baseAsset', '').upper() in spot_bases:
            return None
        oi_notional = None
        try:
            oih = api.get(FUTURES + '/futures/data/openInterestHist',
                          {'symbol': sym, 'period': '1d', 'limit': 2})
            if isinstance(oih, list) and oih:
                oi_notional = num(oih[-1].get('sumOpenInterestValue'))
        except Exception:
            pass
        if oi_notional is None or oi_notional < min_oi:
            return None
        return {'symbol': t.get('symbol'), 'futures': sym, 'address': t.get('contractAddress', ''),
                'oi_notional': oi_notional}

    candidates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for r in pool.map(phase1, pairs):
            if r:
                candidates.append(r)
    candidates.sort(key=lambda r: -r['oi_notional'])
    print(f'阶段1: OI≥{min_oi/1e6:.0f}M 且未上现货的候选 {len(candidates)} 个', flush=True)

    matches = []
    for i, c in enumerate(candidates):
        raw = goplus(api, c['address'], DATA / 'goplus')
        ct = cleaned_top10(raw)
        if ct is not None and ct >= min_top10:
            c['cleaned_top10'] = round(ct, 4)
            c['tier'] = 'S' if ct >= 0.60 else ('A' if ct >= 0.50 else 'B')
            matches.append(c)
        time.sleep(GOPLUS_DELAY)

    # 妖币潜力排序: 清洗后大户集中度(控盘)为主, 持仓名义为辅。
    matches.sort(key=lambda r: (-r['cleaned_top10'], -r['oi_notional']))
    cur = {r['symbol']: r for r in matches}

    prev = {}
    if SNAPSHOT.exists():
        try:
            prev = {x['symbol']: x for x in json.loads(SNAPSHOT.read_text(encoding='utf-8'))}
        except Exception:
            prev = {}
    DATA.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(matches, ensure_ascii=False, indent=2))

    new = [s for s in cur if s not in prev]
    gone = [s for s in prev if s not in cur]

    header = f'妖币候选 @ {time.strftime("%m-%d %H:%M UTC", time.gmtime())}'
    cond = (f'条件: Alpha+合约+未上现货, 持仓≥{min_oi/1e6:.0f}M, 清洗Top10≥{min_top10*100:.0f}%\n'
            f'分级: S(≥60%)触发启动/回撤信号, A(50-60%)观察, B(40-50%)仅记录')
    # 完整候选: OI≥5M未上现的典型有清洗集中度的进 matches, S/A/B 是从中筛选
    sa = [r for r in matches if r.get('tier') in ('S', 'A')]
    if sa:
        n_s = sum(1 for r in matches if r.get('tier') == 'S')
        n_a = sum(1 for r in matches if r.get('tier') == 'A')
        n_b = sum(1 for r in matches if r.get('tier') == 'B')
        body_lines = [header, cond,
                      f'完整清单: {len(matches)}个候选(有清洗集中度), 筛出 S={n_s}/A={n_a}/B={n_b}',
                      '妖币候选排序(按大户集中度):']
        for i, r in enumerate(sa, 1):
            body_lines.append(f"{i}. [{r.get('tier','?')}] {r['symbol']}  清洗Top10 {r['cleaned_top10']*100:.1f}%  持仓 {r['oi_notional']/1e6:.1f}M")
        b_only = [r for r in matches if r.get('tier') == 'B']
        if b_only:
            body_lines.append(f"(仅记录不推送的B级 {len(b_only)} 个: {', '.join(x['symbol'] for x in b_only)})")
    else:
        body_lines = [header, cond, '当前无 S/A 级命中']
    full = '\n'.join(body_lines)
    print(full)

    # 通知状态(上次推送时间戳)
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:
            state = {}
    last_notify = state.get('last_notify', 0)

    changed = bool(new or gone) and bool(prev)  # 首次运行不算"变化"
    if changed:
        diff = []
        if new:
            diff.append('新增: ' + ', '.join(f"{cur[s]['symbol']}({cur[s]['cleaned_top10']*100:.0f}%)" for s in sorted(new)))
        if gone:
            diff.append('退出: ' + ', '.join(gone))
        msg = header + '\n' + '\n'.join(diff) + '\n\n' + ('当前命中: ' + ', '.join(f"{r['symbol']}({r['cleaned_top10']*100:.0f}%)" for r in matches) if matches else '当前无命中')
        # Telegram + 钉钉
        if token and chat:
            try:
                telegram_send(token, chat, msg)
                print('telegram: sent')
            except Exception as e:
                print('telegram send failed:', str(e)[:120])
        try:
            dingtalk_send(env, msg)
            print('dingtalk: sent')
        except Exception as e:
            print('dingtalk send failed:', str(e)[:120])
        state['last_notify'] = time.time()
    elif not prev and matches:
        # 首次运行且有命中: 推一次当前命中(Telegram + 钉钉)
        if token and chat:
            try:
                telegram_send(token, chat, full)
                print('telegram: sent (首次)')
            except Exception as e:
                print('telegram send failed:', str(e)[:120])
        try:
            dingtalk_send(env, full)
            print('dingtalk: sent (首次)')
        except Exception as e:
            print('dingtalk send failed:', str(e)[:120])
        state['last_notify'] = time.time()
    elif time.time() - last_notify >= HEARTBEAT_SECONDS:
        # 长时间无推送 → 仅 Telegram 轻提醒
        if token and chat:
            try:
                telegram_send(token, chat, header + '\n监控正常, 今日无妖币候选。')
                print('telegram: heartbeat sent')
            except Exception as e:
                print('telegram heartbeat failed:', str(e)[:120])
        state['last_notify'] = time.time()
    else:
        # 未变更但已超过排序推送间隔 → 只发 Telegram 当前排序(保持新鲜度, 不碰钉钉)
        rank_every = num(env.get('DEMON_RANK_EVERY_SECONDS', str(HEARTBEAT_SECONDS))) or HEARTBEAT_SECONDS
        if time.time() - state.get('last_rank_notify', 0) >= rank_every:
            body = full if sa else (header + '\n监控正常, 当前无妖币候选。')
            if token and chat:
                try:
                    telegram_send(token, chat, body)
                    print('telegram: ranking sent')
                except Exception as e:
                    print('telegram ranking failed:', str(e)[:120])
            try:
                dingtalk_send(env, body)
                print('dingtalk: ranking sent')
            except Exception as e:
                print('dingtalk ranking failed:', str(e)[:120])
            state['last_rank_notify'] = time.time()
        else:
            print('无变化, 静默(不推送)')

    STATE.write_text(json.dumps(state))


if __name__ == '__main__':
    main()
