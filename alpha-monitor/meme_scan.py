#!/usr/bin/env python3
"""Meme 币筛选器(统一妖币逻辑)。对 meme-radar 候选分 S/A/B 级。

S级: 清洗后大户集中度 ≥ 50% 且 safety_eligible → 触发启动/回撤/止盈跟踪
A级: 集中度 35-50% 或 score 高 → 观察
B级: 其余 → 仅记录

清洗后集中度: 用 GoPlus is_contract 剔除 LP 池/合约地址后的前10大户占比。
数据源: meme-radar 候选(它的 db) + GoPlus(重新算集中度)。
所有推送 TG + 钉钉。
"""
from __future__ import annotations
import sys, json, time, types, os, concurrent.futures
from pathlib import Path
sys.path.insert(0, '/opt/meme-radar')
from net import get_json
from notify import send_channel

DATA = Path('/var/lib/meme-radar')
MATCHES = DATA / 'meme_matches.json'
STATE = DATA / 'scan_state.json'
GOPLUS_DELAY = 0.4

GOPLUS_CHAIN = {'bsc': '56', 'solana': None, 'robinhood': '4663', 'xlayer': '196', 'arc': '5042', 'stable': '988'}


def secret(env, name):
    p = env.get(name + '_FILE')
    return Path(p).read_text().strip() if p else env.get(name, '').strip()


def make_cfg(env):
    return types.SimpleNamespace(
        telegram_enabled=env.get('MEME_TELEGRAM_ENABLED', 'false').strip().lower() == 'true',
        telegram_token=secret(env, 'MEME_TELEGRAM_BOT_TOKEN'),
        telegram_chat=secret(env, 'MEME_TELEGRAM_CHAT_ID'),
        dingtalk_enabled=env.get('MEME_DINGTALK_ENABLED', 'false').strip().lower() == 'true',
        dingtalk_webhook=secret(env, 'MEME_DINGTALK_WEBHOOK'),
        dingtalk_secret=secret(env, 'MEME_DINGTALK_SECRET'),
        dingtalk_keyword=env.get('MEME_DINGTALK_KEYWORD', 'meme').strip(),
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


def get_candidates():
    try:
        import sqlite3
        c = sqlite3.connect(str(DATA / 'meme-radar.sqlite'))
        r = c.execute("SELECT value FROM runtime WHERE key='latest'").fetchone()
        if not r:
            return []
        return json.loads(r[0]).get('rows', [])
    except Exception:
        return []


def cleaned_top10(chain, address):
    """GoPlus 清洗后集中度。返回 0..1 或 None(数据不可靠/不支持)。"""
    chain_id = GOPLUS_CHAIN.get(chain)
    try:
        if chain == 'solana':
            raw = get_json(f'https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses={address}', 10)
            res = raw.get('result', {})
            token = res.get(address) or res.get(str(address).lower()) or {}
        else:
            if not chain_id:
                return None
            raw = get_json(f'https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}', 10)
            res = raw.get('result', {})
            token = res.get(address.lower()) or res.get(address) or {}
        if str(raw.get('code')) != '1' or not isinstance(token, dict) or not token:
            return None
        holders = token.get('holders') or []
        vals = []
        for h in holders:
            if not isinstance(h, dict):
                continue
            v = h.get('percent')
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if not (0 < v <= 1):
                return None  # 脏数据
            if str(h.get('is_contract')) in ('1', 'true'):
                continue
            vals.append(v)
        s = sum(sorted(vals, reverse=True)[:10])
        return s if s <= 1.0 else None
    except Exception:
        return None


def main():
    import os
    env = dict(os.environ)
    cands = get_candidates()
    if not cands:
        print('无 meme 候选, 跳过')
        return

    # 算清洗后集中度(限流保护: 串行 + 延迟)
    for c in cands:
        chain = c.get('chain')
        addr = c.get('address')
        if chain in GOPLUS_CHAIN:
            c['_ct'] = cleaned_top10(chain, addr)
            time.sleep(GOPLUS_DELAY)
        else:
            c['_ct'] = None
        s = c.get('score_meta', {}).get('score', 0) if isinstance(c.get('score_meta'), dict) else 0
        c['_score'] = s
        # 分级(混合): BSC/Solana 用清洗后集中度(像妖币); 其他链用 score+聪明钱兜底
        s = c['_score']
        elig = c.get('safety_eligible')
        liq = c.get('liquidity_usd') or 0
        smart = c.get('smart_wallet_hits') or 0
        vol1h = c.get('volume_1h') or 0
        ct = c.get('_ct')
        has_ct = ct is not None  # GoPlus 对该币有没有 holders 数据
        if has_ct:
            # BSC/Solana: 像妖币, 用清洗后大户集中度
            if ct >= 0.50 and elig:
                c['_tier'] = 'S'
            elif ct >= 0.35 or s >= 40:
                c['_tier'] = 'A'
            else:
                c['_tier'] = 'B'
        else:
            # 其他链(robinhood/arc等): score + 安全 + 流动性 + 聪明钱
            if s >= 50 and elig and liq >= 30000 and (smart >= 1 or vol1h >= 50000):
                c['_tier'] = 'S'
            elif s >= 35 and elig:
                c['_tier'] = 'A'
            else:
                c['_tier'] = 'B'
    sa = [c for c in cands if c['_tier'] in ('S', 'A')]
    sa.sort(key=lambda c: (-(c['_ct'] if c.get('_ct') is not None else -1), -(c['_score'] or 0)))

    # 保存 matches(S级, 供 meme_track 用)
    s_tier = [{'symbol': c.get('symbol'), 'chain': c.get('chain'), 'address': c.get('address'),
               'score': c['_score'], 'smart_hits': c.get('smart_wallet_hits',0), 'tier': 'S'} for c in sa if c['_tier'] == 'S']
    MATCHES.write_text(json.dumps(s_tier, ensure_ascii=False, indent=2))

    ts = time.strftime('%m-%d %H:%M UTC', time.gmtime())
    prev = {}
    if MATCHES.with_suffix('.prev.json').exists():
        try:
            prev = {x['symbol']: x for x in json.loads(MATCHES.with_suffix('.prev.json').read_text())}
        except Exception:
            pass
    cur = {x['symbol']: x for x in s_tier}
    new = [s for s in cur if s not in prev]
    gone = [s for s in prev if s not in cur]
    MATCHES.with_suffix('.prev.json').write_text(json.dumps(s_tier, ensure_ascii=False))

    header = f'🌊 Meme S级候选 @ {ts}'
    cond = '条件: BSC/Solana用清洗后大户集中度(≥50%), 其他链用score+安全+聪明钱; S级触发信号'
    if sa:
        body_lines = [header, cond, 'Meme候选排序(S+A级, 按大户集中度):']
        for i, c in enumerate(sa, 1):
            if c.get('_ct') is not None:
                body_lines.append(f"{i}. [{c['_tier']}] {c.get('symbol','?')} ({c.get('chain','?')}) 集中度{c['_ct']*100:.0f}% score {c['_score']:.0f}")
            else:
                liq_w = (c.get('liquidity_usd') or 0) / 1000
                sm = c.get('smart_wallet_hits') or 0
                body_lines.append(f"{i}. [{c['_tier']}] {c.get('symbol','?')} ({c.get('chain','?')}) score {c['_score']:.0f} 流动性{liq_w:.0f}K 聪明钱{sm}")
        b = [c for c in cands if c['_tier'] == 'B']
        if b:
            body_lines.append(f"(B级仅记录 {len(b)} 个: {', '.join(x.get('symbol','?') for x in b[:8])})")
    else:
        body_lines = [header, cond, '当前无 S/A 级命中']
    full = '\n'.join(body_lines)
    print(full)

    # 变化时推, 或每天一次汇总
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    changed = bool(new or gone) and bool(prev)
    if changed:
        diff = []
        if new:
            diff.append('新增S级: ' + ', '.join(f"{cur[s]['symbol']}({cur[s]['cleaned_top10']*100:.0f}%)" for s in sorted(new)))
        if gone:
            diff.append('退出: ' + ', '.join(gone))
        body = header + '\n' + '\n'.join(diff)
        if sa:
            body += '\n\n当前S+A级: ' + ', '.join(f"{c.get('symbol')}({c['_tier']})" for c in sa)
        notify(env, body)
        state['last_notify'] = time.time()
    elif time.time() - state.get('last_notify', 0) >= 24 * 3600:
        notify(env, full)
        state['last_notify'] = time.time()
    else:
        print('无变化, 静默')
    STATE.write_text(json.dumps(state))


if __name__ == '__main__':
    main()
