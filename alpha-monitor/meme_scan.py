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
        if not vals:
            return None  # 无有效外部大户数据(全为合约/空), 视作未知而非 0
        s = sum(sorted(vals, reverse=True)[:10])
        return s if s <= 1.0 else None
    except Exception:
        return None


def meme_rush_candidates():
    """币安官方 meme-rush: 待迁移(Finalizing)+已迁移(Migrated), BSC/Solana。
    自带 holdersTop10Percent(集中度)与 smartMoney/kol/pro/bn 持仓人数, 补 GoPlus 对新币无数据的缺口。"""
    try:
        import bn_skills
    except Exception:
        return []
    out = []
    for chain_name, chain_id in (('bsc', bn_skills.CHAIN_BSC), ('solana', bn_skills.CHAIN_SOL)):
        for rank in (bn_skills.RUSH_FINALIZING, bn_skills.RUSH_MIGRATED):
            try:
                rows = bn_skills.meme_rush(chain_id=chain_id, rank_type=rank, limit=30)
            except Exception:
                continue
            for row in rows:
                try:
                    pr = bn_skills.to_pair(row, chain_name)
                except Exception:
                    pr = None
                if pr and pr.get('address'):
                    out.append(pr)
    # 按聪明钱持仓人数取前 40(避免上百候选淹没; 聪明钱是最有信息量的维度)
    out.sort(key=lambda x: -(x.get('smart_money_holders', 0) + x.get('kol_holders', 0) + x.get('pro_holders', 0)))
    return out[:40]


def main():
    import os
    env = dict(os.environ)
    cands = get_candidates()
    # 合并币安官方 meme-rush 候选(自带集中度+聪明钱, 解决 GoPlus 新币无数据)
    rush = meme_rush_candidates()
    _seen = {(c.get('chain'), str(c.get('address', '')).lower()) for c in cands}
    for r in rush:
        k = (r.get('chain'), str(r.get('address', '')).lower())
        if k in _seen:
            continue
        _seen.add(k)
        r['score_meta'] = {'score': 0}
        r['_from_rush'] = True
        cands.append(r)
    print(f'候选: meme-radar {len(cands) - len(rush)} + meme-rush 新增, 合计 {len(cands)}')
    if not cands:
        print('无 meme 候选, 跳过')
        return

    # 算清洗后集中度(限流保护: 串行 + 延迟)
    for c in cands:
        chain = c.get('chain')
        addr = c.get('address')
        if c.get('_from_rush'):
            # meme-rush 自带集中度(百分数)与聪明钱持仓人数, 不查 GoPlus
            top10 = c.get('holders_top10_pct')
            c['_ct'] = (top10 / 100.0) if top10 is not None else None
            c['_smart'] = (c.get('smart_money_holders', 0) + c.get('kol_holders', 0)
                           + c.get('pro_holders', 0) + (c.get('bn_holders', 0) or 0) * 2)
            c['_score'] = 0
            liq = c.get('liquidity_usd') or 0
            ct = c['_ct']
            smart = c['_smart']
            sniper = c.get('holders_sniper_pct') or 0   # 狙击机器人占比: 高则开盘即砸
            dev = c.get('holders_dev_pct') or 0          # dev 持仓占比: 高则随时可砸
            # 风险过滤: 狙击手/DEV 占比过高直接降级(不管聪明钱多少)
            risky = (sniper >= 50) or (dev >= 20)
            # 分级: 聪明钱在场 + 流动性充足 + 集中度不极端 + 无高砸盘风险
            if smart >= 5 and liq >= 30000 and (ct is None or ct <= 0.80) and not risky:
                c['_tier'] = 'S'
            elif smart >= 2 and liq >= 10000 and not risky:
                c['_tier'] = 'A'
            else:
                c['_tier'] = 'B'
            c['_risky'] = risky
            continue
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
    sa.sort(key=lambda c: (0 if c['_tier'] == 'S' else 1, -(c['_ct'] if c.get('_ct') is not None else -1), -(c.get('_smart', 0))))

    # 保存 matches(S+A 都写, 供 meme_track 跟踪与日更; S 级才触发信号)
    s_tier = [{'symbol': c.get('symbol'), 'chain': c.get('chain'), 'address': c.get('address'),
               'score': c.get('_score', 0),
               'liquidity_usd': c.get('liquidity_usd'),
               'price_usd': c.get('price_usd'),
               'cleaned_top10': c.get('_ct'),
               'smart': c.get('_smart', c.get('smart_wallet_hits', 0)),
               'futures': c.get('futures'),
               'tier': c.get('_tier', 'B')}
              for c in sa]
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
        n_s = sum(1 for c in cands if c['_tier'] == 'S')
        n_a = sum(1 for c in cands if c['_tier'] == 'A')
        n_b = sum(1 for c in cands if c['_tier'] == 'B')
        body_lines = [header, cond,
                      f'完整清单: {len(cands)}个候选, 筛出 S={n_s}/A={n_a}/B={n_b}',
                      'Meme候选排序(S+A级):']
        for i, c in enumerate(sa[:10], 1):
            if c.get('_from_rush'):
                sm = int(c.get('_smart', 0))
                body_lines.append(f"{i}. [{c['_tier']}] {c.get('symbol','?')} ({c.get('chain','?')}) 集中度{c['_ct']*100:.0f}% 聪明钱{sm}人\n   合约 {c.get('address','')}")
                continue
            elif c.get('_ct') is not None:
                body_lines.append(f"{i}. [{c['_tier']}] {c.get('symbol','?')} ({c.get('chain','?')}) 集中度{c['_ct']*100:.0f}% score {c['_score']:.0f}")
            else:
                liq_w = (c.get('liquidity_usd') or 0) / 1000
                sm = c.get('smart_wallet_hits') or 0
                body_lines.append(f"{i}. [{c['_tier']}] {c.get('symbol','?')} ({c.get('chain','?')}) score {c['_score']:.0f} 流动性{liq_w:.0f}K 聪明钱{sm}")
        if len(sa) > 10:
            body_lines.append(f"(S+A 共 {len(sa)} 个, 以上为前 10)")
        b = [c for c in cands if c['_tier'] == 'B']
        if b:
            body_lines.append(f"(B级仅记录 {len(b)} 个)")
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
            diff.append('新增: ' + ', '.join(
                f"{cur[s]['symbol']}({(cur[s].get('cleaned_top10') or 0)*100:.0f}%)" for s in sorted(new)))
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
