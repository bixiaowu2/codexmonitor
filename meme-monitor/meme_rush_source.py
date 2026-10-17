"""meme-rush 独立来源 —— 只往账本登记数据, 为"bonding curve 阶段是否更值得"积累前瞻样本。

【设计: 纯旁路】
· **不改 radar.py 一行** —— 不参与评分、不进 S/A 分级、不触发任何推送。
  理由: 现有 safety 检查对 bonding curve 阶段的币**不适用**(没有 DEX 池、没有 LP 锁定概念),
  直接进名单会把一批未经验证的极高风险标的推给用户。
· 只调用 `forward.register()` 把样本写进 `forward_tracks`。
· 同侪的 `forward-observer`(每5分钟)会自动发现这些新记录并在检查点到点时取价 —— 不用协调。
· 攒够样本后用 `forward-report` 比较三个阶段, 再决定谁能进 S/A。

【为什么值得接 —— 实测交叉验证】
拿 900 个地址去比雷达 `pairs` 表的 17.5 万个地址(2026-10-16):
    solana: New 4% | Finalizing 44% | Migrated 61%
    bsc   : New 5% | Finalizing 16% | Migrated 84%
`New` 阶段 **95% 的币雷达从没见过** —— 确实覆盖了现有扫描(GeckoTerminal trending/new_pools,
即"已上 DEX")**前面那一段**。

【两个实测结论, 别踩】
1. ⚠️ SKILL.md 说 rankType=20 是"曲线快满即将迁移", **实测 progress 中位只有 29.3%**。
   阶段排序对(0% → 29% → 100%), 但别按 "progress>80%" 这类条件筛, 会一条都捞不到。
2. ⚠️ 风险标签几乎不出(tagDevWashTrading 覆盖 0%) —— **不能靠它做安全过滤**。

用法:
    meme_rush_source.py              # 登记一轮
    meme_rush_source.py --dry-run    # 只看捞到什么, 不写库
    meme_rush_source.py --stats      # 看已登记样本的分布
"""
from __future__ import annotations

import json, os, sqlite3, sys, time
import urllib.request
from pathlib import Path

sys.path.insert(0, '/opt/meme-radar')
import forward                                              # noqa: E402

ENDPOINT = ('https://web3.binance.com/bapi/defi/v1/public/wallet-direct/'
            'buw/wallet/market/token/pulse/rank/list/ai')
HEADERS = {'Content-Type': 'application/json',
           'User-Agent': 'binance-web3/2.0 (Skill)',
           'clienttype': 'web'}
STAGES = {10: 'new', 20: 'finalizing', 30: 'migrated'}      # launchpad 生命周期阶段
CHAINS = {'solana': 'CT_501', 'bsc': '56', 'base': '8453'}

DATA = Path(os.environ.get('MEME_DATA', '/var/lib/meme-radar'))
DB = DATA / 'meme-radar.sqlite'

TOP_N = int(os.environ.get('MR_TOP_N', '5'))          # 每个(链,阶段)取前几条
BUCKET = int(os.environ.get('MR_BUCKET_SECONDS', '7200'))   # 2 小时一个桶(同桶内只登记一次)

# 只保留对研究有用的字段, 全部原样存(不做任何加工, 免得以后想换口径时回不去)
NUMERIC = ('holdersTop10Percent', 'holdersSniperPercent', 'holdersInsiderPercent',
           'holdersDevPercent', 'bundlerHoldingPercent', 'newWalletHoldingPercent',
           'bnHoldingPercent', 'progress', 'marketCap', 'liquidity', 'volume',
           'price', 'priceChange', 'priceChange24h', 'devSellPercent',
           'smartMoneyHoldingPercent', 'taxRate')
COUNT = ('holders', 'count', 'countBuy', 'countSell', 'smartMoneyHolders',
         'bnHolders', 'kolHolders', 'proHolders', 'migrateStatus', 'migrateTime',
         'createTime', 'protocol', 'paidOnDexScreener', 'exclusive', 'devMigrateCount')
TAGS = ('tagDevBurnedToken', 'tagDevWashTrading', 'tagInsiderWashTrading', 'tagPumpfunLive')


def _num(v):
    try:
        n = float(v)
        return n if n == n and abs(n) != float('inf') else None
    except (TypeError, ValueError):
        return None


def fetch(chain_id, rank, limit=TOP_N):
    body = json.dumps({'chainId': chain_id, 'rankType': rank, 'limit': limit}).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.loads(r.read())
    if d.get('code') != '000000':
        return []
    return d.get('data') or []


def features_of(row):
    f = {}
    for k in NUMERIC:
        n = _num(row.get(k))
        if n is not None:
            f[k] = n
    for k in COUNT:
        v = row.get(k)
        if v is not None:
            f[k] = v
    for k in TAGS:
        if row.get(k):
            f[k] = 1
    for k in ('symbol', 'name', 'narrativeText', 'twitterHandle'):
        v = row.get(k)
        if v:
            f[k] = str(v)[:200]
    soc = row.get('socials') or {}
    if isinstance(soc, dict):
        f['socials'] = ','.join(sorted(k for k, v in soc.items() if v))[:120]
    return f


def run_once(dry=False, verbose=True):
    if not DB.exists():
        print('找不到账本', DB)
        return 2
    db = sqlite3.connect(str(DB), timeout=20)
    db.row_factory = sqlite3.Row
    forward.initialize(db)

    now = time.time()
    bucket = int(now // BUCKET)
    registered = seen = skipped = 0
    tally = {}
    for chain, cid in CHAINS.items():
        for rank, stage in STAGES.items():
            try:
                rows = fetch(cid, rank)
            except Exception as e:
                if verbose:
                    print(f'  {chain}/{stage} 取失败: {type(e).__name__}: {str(e)[:70]}')
                continue
            for row in rows:
                seen += 1
                addr = str(row.get('contractAddress') or '').strip()
                price = _num(row.get('price'))
                if not addr or not price or price <= 0:
                    skipped += 1
                    continue
                key = f'meme-rush-v1:{stage}:{chain}:{addr.lower()}:{bucket}'
                instrument = f'{chain}:{addr if chain == "solana" else addr.lower()}'
                payload = {'kind': 'meme_rush_observation', 'stage': stage, 'rank_type': rank,
                           'chain': chain, 'source': 'meme_rush',
                           'features': features_of(row)}
                if not dry:
                    forward.register(db, key, instrument, str(row.get('symbol') or '?'),
                                     chain, price, payload, now)
                registered += 1
                tally[stage] = tally.get(stage, 0) + 1
    if not dry:
        db.commit()
    db.close()
    if verbose:
        print(f'{"[dry-run] " if dry else ""}捞到 {seen} | 登记 {registered} | 跳过(无地址/无价) {skipped}')
        print(f'  阶段分布: {tally}')
    return 0


def stats():
    db = sqlite3.connect(str(DB), timeout=20)
    db.row_factory = sqlite3.Row
    n = 0
    by = {}
    for r in db.execute("SELECT payload FROM forward_tracks WHERE payload LIKE '%meme_rush_observation%'"):
        n += 1
        d = json.loads(r['payload'] or '{}')
        s = d.get('stage', '?')
        by.setdefault(s, {'n': 0, 'mc': [], 'ct': [], 'prog': []})
        f = d.get('features') or {}
        by[s]['n'] += 1
        for k, dst in (('marketCap', 'mc'), ('holdersTop10Percent', 'ct'), ('progress', 'prog')):
            if f.get(k) is not None:
                by[s][dst].append(f[k])
    db.close()
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0
    print(f'meme-rush 已登记 {n} 条')
    print(f"{'阶段':<12}{'条数':>6}{'市值中位':>14}{'集中度中位':>12}{'进度中位':>10}")
    for s in ('new', 'finalizing', 'migrated'):
        if s not in by:
            continue
        v = by[s]
        print(f"{s:<12}{v['n']:>6}{med(v['mc']):>14,.0f}{med(v['ct']):>11.1f}%{med(v['prog']):>9.1f}%")


if __name__ == '__main__':
    if '--stats' in sys.argv:
        stats()
    else:
        sys.exit(run_once(dry='--dry-run' in sys.argv))
