#!/usr/bin/env python3
"""聪明钱包发现 —— 从币安官方排行榜筛优质钱包, 写入 meme 雷达的 smart_wallets.json。

**为什么要做**: meme 雷达的 `inputs.wallet_index()` 会读 `smart_wallets.json` 来算
"聪明钱包命中数"(`smart_wallet_hits`), 但该文件**从未存在** —— 也就是说这一维
一直是 0, 白白浪费。本脚本用币安官方排行榜把它填上。

**数据源**: `GET /api/v1/dex/market/leaderboard/list`(走 mihomo 代理 —— 有地区限制)
  参数: binanceChainId / timeFrame(1=7d,2=30d,3=90d) / sortBy(1~5) / page / size(<=20)
  返回: {cursor, items:[{walletAddress, winRatePercent, realizedPnlUsd, txs,
                         topPnlTokenList:[{tokenContractAddress, tokenSymbol, ...}]}]}

**输出格式**(必须匹配 meme-radar/inputs.py 的 valid_event + enrich):
  [{"address": 钱包, "token": 代币合约地址, "chain": "bsc"|"solana",
    "observed_at": 时间戳(必须 24h 内), "url": "https://...", "source": "https://..."}]
  enrich 的匹配规则: 事件的 chain 与候选一致, 且 address(token) == 候选的合约地址。

**注意**: observed_at 必须在 24 小时内, 所以本脚本要**每天跑**(配 timer)。
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

sys.path.insert(0, '/opt/alpha-radar')
import web3_client as w3

OUT = Path('/var/lib/meme-radar/smart_wallets.json')
CACHE = Path('/var/lib/meme-radar/smart_wallets_cache.json')

# 筛选门槛(可调)
MIN_WINRATE = 0.55      # 胜率 >= 55%
MIN_PNL_USD = 10_000    # 已实现盈亏 >= 1 万美元
MIN_TXS = 20            # 交易数 >= 20(太少的样本不可信)
MAX_PER_CHAIN = 120     # 每链最多收录多少个钱包(控制规模)

CHAINS = [('bsc', w3.CHAIN_BSC), ('solana', w3.CHAIN_SOL)]
TIMEFRAMES = ('1', '2')   # 7d, 30d
SORTBYS = ('1', '2', '3', '4', '5')

EXPLORER = {
    'bsc': 'https://bscscan.com/token/',
    'solana': 'https://solscan.io/token/',
}


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_chain(chain, cid):
    """拉该链的排行榜(多组合去重), 返回 {wallet: item}。"""
    best = {}
    for tf in TIMEFRAMES:
        for sb in SORTBYS:
            try:
                d = w3.call('GET', '/api/v1/dex/market/leaderboard/list',
                            {'binanceChainId': cid, 'timeFrame': tf, 'sortBy': sb,
                             'page': 1, 'size': 20})
            except Exception as e:
                print(f'  {chain} tf={tf} sb={sb} 失败: {str(e)[:80]}')
                continue
            items = (d or {}).get('items') or []
            for it in items:
                a = str(it.get('walletAddress') or '').lower()
                if a and a not in best:
                    best[a] = it
    return best


def qualify(it):
    wr = f(it.get('winRatePercent'))
    pnl = f(it.get('realizedPnlUsd'))
    txs = f(it.get('txs'))
    if wr is None or pnl is None or txs is None:
        return False
    return wr >= MIN_WINRATE and pnl >= MIN_PNL_USD and txs >= MIN_TXS


def main():
    now = time.time()
    events, stats = [], {}
    for chain, cid in CHAINS:
        wallets = fetch_chain(chain, cid)
        good = {a: it for a, it in wallets.items() if qualify(it)}
        # 按盈亏降序取前 N
        ordered = sorted(good.items(), key=lambda kv: -(f(kv[1].get('realizedPnlUsd')) or 0))
        ordered = ordered[:MAX_PER_CHAIN]
        n_ev = 0
        for a, it in ordered:
            for t in (it.get('topPnlTokenList') or []):
                ca = str(t.get('tokenContractAddress') or '').strip()
                if not ca:
                    continue
                url = EXPLORER.get(chain, 'https://') + ca
                events.append({
                    'address': a,
                    'token': ca,
                    'chain': chain,
                    'observed_at': now,
                    'url': url,
                    'source': url,
                    'symbol': t.get('tokenSymbol'),
                    'winrate': f(it.get('winRatePercent')),
                    'pnl_usd': f(it.get('realizedPnlUsd')),
                    'wallet_name': it.get('walletName') or '',
                })
                n_ev += 1
        stats[chain] = {'拉取': len(wallets), '达标': len(good), '收录': len(ordered), '事件': n_ev}
        print(f'  {chain}: 拉取 {len(wallets)} / 达标 {len(good)} / 收录 {len(ordered)} / 生成事件 {n_ev}')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(events, ensure_ascii=False, indent=1))
    CACHE.write_text(json.dumps({'ts': now, 'stats': stats}, ensure_ascii=False))
    print(f'已写 {OUT}: {len(events)} 条事件')
    print(f'门槛: 胜率>={MIN_WINRATE:.0%} 盈亏>=${MIN_PNL_USD} 交易数>={MIN_TXS}')


if __name__ == '__main__':
    main()
