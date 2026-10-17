#!/usr/bin/env python3
"""币安官方 Web3 skills 封装(binance-skills-hub)。

提供三个数据源(均为币安官方公开接口, 无需 API key):
  meme_rush()     — meme 币生命周期(新发/待迁移/已迁移), 含集中度/聪明钱/KOL/币安钱包持仓
  topic_rush()    — AI 热点叙事 + 关联代币(按净流入排序)
  smart_money()   — 聪明钱买卖信号(含 isAlpha 标记/触发价/当前价/最大涨幅/退出率)
  smart_money_inflow() — 聪明钱净流入榜(含市值/流动性/集中度/风险码/KYC持有者)

用 bun 运行官方 cli.mjs。所有返回已是解析后的 list。
"""
from __future__ import annotations
import json, subprocess
from pathlib import Path

BUN = '/usr/local/bin/bun'
SKILLS = Path('/opt/bn-skills')

# 链 ID
CHAIN_BSC = '56'
CHAIN_SOL = 'CT_501'
CHAIN_BASE = '8453'

# meme-rush rankType
RUSH_NEW = 10        # 新发(还在 bonding curve)
RUSH_FINALIZING = 20 # 即将迁移(bonding curve 接近完成) ← 拉升前窗口
RUSH_MIGRATED = 30   # 刚迁移到 DEX

# topic-rush rankType
TOPIC_LATEST = 10
TOPIC_RISING = 20


class SkillError(RuntimeError):
    pass


def _call(skill, cmd, params, timeout=35):
    script = SKILLS / skill / 'scripts' / 'cli.mjs'
    if not script.exists():
        raise SkillError(f'{skill} skill 未安装({script})')
    try:
        r = subprocess.run([BUN, str(script), cmd, json.dumps(params, ensure_ascii=False)],
                           capture_output=True, text=True, timeout=timeout,
                           cwd=str(SKILLS / skill))
    except subprocess.TimeoutExpired:
        raise SkillError(f'{skill}:{cmd} timeout') from None
    out = (r.stdout or '').strip()
    if not out:
        raise SkillError(f'{skill}:{cmd} empty response: {(r.stderr or "")[:120]}')
    try:
        d = json.loads(out)
    except ValueError:
        raise SkillError(f'{skill}:{cmd} bad json: {out[:120]}') from None
    if str(d.get('code')) not in ('000000', '0'):
        raise SkillError(f"{skill}:{cmd} code={d.get('code')} {d.get('message') or ''}")
    data = d.get('data')
    return data if isinstance(data, list) else ([] if data is None else [data])


def meme_rush(chain_id=CHAIN_BSC, rank_type=RUSH_FINALIZING, limit=20, **filters):
    """launchpad 生命周期榜。filters 支持 progress/holders/liquidity/marketCap/
    holdersTop10Percent/bnHolders/kolHolders/proHolders 等 min/max 对。"""
    p = {'chainId': str(chain_id), 'rankType': int(rank_type), 'limit': int(limit)}
    p.update(filters)
    return _call('meme-rush', 'meme-rush', p)


def topic_rush(chain_id=CHAIN_BSC, rank_type=TOPIC_RISING, sort=20, **filters):
    """AI 热点叙事 + 关联代币。sort: 10=创建时间 20=净流入。"""
    p = {'chainId': str(chain_id), 'rankType': int(rank_type), 'sort': int(sort)}
    p.update(filters)
    return _call('meme-rush', 'topic-rush', p)


def smart_money(chain_id=CHAIN_BSC, page=1, page_size=50):
    """聪明钱买卖信号。返回含 isAlpha/direction/smartMoneyCount/alertPrice/
    currentPrice/highestPrice/exitRate/tokenTag 等。"""
    return _call('trading-signal', 'smart-money',
                 {'chainId': str(chain_id), 'page': int(page), 'pageSize': int(page_size)})


def num(value):
    """币安返回的百分比字段是预格式化字符串(如 "22.7"), 数值字段也可能是字符串。"""
    try:
        f = float(value)
        return f if f == f and abs(f) != float('inf') else None
    except (TypeError, ValueError):
        return None


def to_pair(row, chain_name):
    """meme-rush 条目 → 雷达 pair 结构(供 meme_scan 复用)。"""
    addr = row.get('contractAddress')
    if not addr:
        return None
    top10 = num(row.get('holdersTop10Percent'))  # 已是百分数(如 22.7)
    return {
        'chain': chain_name,
        'address': addr,
        'symbol': row.get('symbol') or '?',
        'name': row.get('name') or '',
        'price_usd': num(row.get('price')),
        'liquidity_usd': num(row.get('liquidity')),
        'market_cap': num(row.get('marketCap')),
        'volume_1h': num(row.get('volume')),
        'holders': num(row.get('holders')),
        'holders_top10_pct': top10,
        'holders_sniper_pct': num(row.get('holdersSniperPercent')),
        'holders_dev_pct': num(row.get('holdersDevPercent')),
        'smart_money_holders': num(row.get('smartMoneyHolders')) or 0,
        'kol_holders': num(row.get('kolHolders')) or 0,
        'pro_holders': num(row.get('proHolders')) or 0,
        'bn_holders': num(row.get('bnHolders')) or 0,
        'progress': num(row.get('progress')),
        'migrate_status': row.get('migrateStatus'),
        'narrative': (row.get('narrativeText') or {}).get('cn') or (row.get('narrativeText') or {}).get('en') or '',
        'socials': row.get('socials') or {},
        'source': 'binance_meme_rush',
    }



def smart_money_inflow(chain_id=CHAIN_BSC, page=1, page_size=50):
    """聪明钱净流入榜(crypto-market-rank skill)。按净流入排序, 一条里同时带:
    marketCap / liquidity / holdersTop10Percent / tokenRiskLevel+tokenRiskCodes /
    kycHolders / countBuy / inflow / aiNarrativeFlag。"""
    return _call('crypto-market-rank', 'smart-money-inflow',
                 {'chainId': str(chain_id), 'page': int(page), 'pageSize': int(page_size)})


def rank_to_pair(row, chain_name):
    """crypto-market-rank 条目 → 雷达 pair 结构。

    注意字段名与 meme-rush 不同: 用 ca/tokenName, 且**没有 smartMoneyHolders**;
    这里用 kycHolders(币安 KYC 认证持有者数)作为"可信钱包数"的代理。
    """
    addr = row.get('ca')
    if not addr:
        return None
    return {
        'chain': chain_name,
        'address': addr,
        'symbol': row.get('tokenName') or '?',
        'price_usd': num(row.get('price')),
        'liquidity_usd': num(row.get('liquidity')),
        'market_cap': num(row.get('marketCap')),
        'volume_1h': num(row.get('volume')),
        'holders': num(row.get('holders')),
        'holders_top10_pct': num(row.get('holdersTop10Percent')),
        'smart_money_holders': 0,
        'source': 'binance_market_rank',
        '_kyc': num(row.get('kycHolders')) or 0,
        '_buy': num(row.get('countBuy')) or 0,
        '_inflow': num(row.get('inflow')),
        '_risk_level': num(row.get('tokenRiskLevel')),
        '_risk_codes': row.get('tokenRiskCodes') or [],
        '_ai_narrative': row.get('aiNarrativeFlag'),
    }

if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'test'
    if cmd == 'test':
        for name, fn in (('meme_rush(待迁移)', lambda: meme_rush(limit=2)),
                         ('topic_rush(上升)', lambda: topic_rush(limit=2)),
                         ('smart_money', lambda: smart_money(page_size=2))):
            try:
                rows = fn()
                print(f'{name}: {len(rows)} 条')
                if rows:
                    print('  字段:', ', '.join(list(rows[0].keys())[:12]))
            except Exception as e:
                print(f'{name}: 失败 {e}')
