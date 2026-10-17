"""账本特征回填 —— 只做一件事: 给新登记的 forward_tracks 补官方集中度。

【为什么单独一个服务】
· 取价(forward return)由同侪的 `/usr/local/bin/forward-observer` 负责(它更强: 币安 Web3
  端点 + DexScreener, 且借 AMD 的 IP 绕 GeckoTerminal 限流)。不要重复实现。
· 但**特征回填是另一件事**, 而且它一度随我的旧观测器被同名 unit 顶掉而静默停了 ——
  实测 2026-10-16 04:34 之后登记的记录**集中度回填为 0 条**。
· 所以拆成独立服务, 各管各的, 互不覆盖。

【为什么需要集中度】
STATE.md 第十二轮: 用户最想验证的"高控盘对 meme 到底是利好还是风险"用的正是它。
而 radar 的流水线**根本不算集中度**(`p` 里没有 `cleaned_top10`/`holders_top10_pct`),
只能在这里补。用币安官方口径(`/opt/alpha-radar/web3_client.top10_percent`) ——
注意它**不剔除 LP/销毁地址**, 数值偏高, 与 GoPlus 清洗后口径不可直接比。

用法:
    ledger_features_backfill.py            # 补一轮
    ledger_features_backfill.py --dry-run  # 只看要补几条
"""
from __future__ import annotations

import json, os, sqlite3, sys, time
from pathlib import Path

sys.path.insert(0, '/opt/alpha-radar')
try:
    import web3_client as _w3
except Exception:
    _w3 = None

DATA = Path(os.environ.get('MEME_DATA', '/var/lib/meme-radar'))
DB = DATA / 'meme-radar.sqlite'
MAX_FETCH = int(os.environ.get('FEATURE_BACKFILL_MAX', '8'))
MAX_AGE = int(os.environ.get('FEATURE_BACKFILL_AGE', '7200'))    # 只补登记 2 小时内的
# 官方集中度只覆盖这几条链(与 web3_client 的 chain id 一致)
WEB3_CHAIN = {'bsc': '56', 'solana': 'CT_501', 'base': '8453', 'ethereum': '1'}


def candidates(db, now):
    """① 短读: 挑出还没补集中度的候选。读完立刻结束, 不持锁。"""
    out = []
    for r in db.execute("SELECT key, instrument_key, payload FROM forward_tracks "
                        "WHERE created > ? ORDER BY created DESC LIMIT 400", (now - MAX_AGE,)):
        d = json.loads(r['payload'] or '{}')
        f = d.get('features')
        if f is None:                     # 旧格式记录, 没有 features 可补
            continue
        if f.get('concentration_official_pct') is not None:
            continue
        chain, _, addr = r['instrument_key'].partition(':')
        cid = WEB3_CHAIN.get(chain)
        if not cid or not addr:
            continue
        out.append((r['key'], cid, addr, d, f))
        if len(out) >= MAX_FETCH:
            break
    return out


def backfill(db, now, dry=False):
    """两段式: **先在无锁状态下取数, 再一次性短事务写入**。

    关键: 取数(网络)期间**绝不能持有写事务** —— 这个库是 journal_mode=delete,
    写要独占锁, 持锁几秒就会把雷达卡住, 反过来也把自己卡死(database is locked)。
    """
    if _w3 is None:
        print('缺 /opt/alpha-radar/web3_client')
        return 0, 0
    cand = candidates(db, now)
    if not cand:
        return 0, 0
    if dry:
        return len(cand), len(cand)

    # ② 取数 —— 此刻没有任何写事务
    updates = []
    for key, cid, addr, d, f in cand:
        try:
            ct = _w3.top10_percent(cid, addr)
        except Exception:
            continue
        if ct is None:
            continue
        f['concentration_official_pct'] = ct
        d['features'] = f
        updates.append((json.dumps(d, ensure_ascii=False), key))

    # ③ 短写 —— 一次提交, 毫秒级
    for payload, key in updates:
        db.execute("UPDATE forward_tracks SET payload=? WHERE key=?", (payload, key))
    return len(updates), len(cand)


def stats(db, now):
    n = f = ct = mc = 0
    for r in db.execute("SELECT payload FROM forward_tracks WHERE created > ?", (now - 86400,)):
        n += 1
        d = json.loads(r['payload'] or '{}')
        ft = d.get('features')
        if not ft:
            continue
        f += 1
        if ft.get('concentration_official_pct') is not None:
            ct += 1
        if ft.get('market_cap') is not None:
            mc += 1
    print(f'最近 24h: 登记 {n} | 带 features {f} | 含集中度 {ct} | 含市值 {mc}')


if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    now = time.time()
    if not DB.exists():
        print('找不到账本', DB)
        sys.exit(2)
    db = sqlite3.connect(str(DB), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')   # 别人持锁时等待 30s, 别直接报 locked
    try:
        done, seen = backfill(db, now, dry)
        if not dry:
            db.commit()
        print(f'{"[dry-run] " if dry else ""}补集中度 {done} 条(候选 {seen})')
        stats(db, now)
    finally:
        db.close()
