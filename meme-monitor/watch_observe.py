#!/usr/bin/env python3
"""候选观察名单回查（2026-11-01 lead 8f4f / 925a 批准）。

首次见到候选后，按 1h / 6h / 24h 各回查一次价，写入新表
candidate_watch_observations。只增不改：
  · 不碰 forward_tracks / candidates_archive / pairs / signals
  · 不动榜单采集逻辑与事件过滤器
  · 不产生交易信号，不发任何通知

数据来源：
  · 首次见到时间与首次价格 —— /var/lib/meme-radar/candidates_history/*.jsonl
    （meme_scan 的候选池留痕，原文照抄，不改写）
  · 回查价 —— DexScreener /tokens/v1/{chain}/{addr,...}（每批至多 30 地址）

退避：复用 sources.Collector 的 backoff/failures 语义（60*2^(n-1)，上限 900s），
      状态落 /var/lib/meme-radar/watch_backoff.json，跨轮保持。
"""
from __future__ import annotations
import json, os, sqlite3, sys, time
from pathlib import Path

sys.path.insert(0, '/opt/meme-radar')
from net import get_json, FetchError
from sources import Collector

DATA = Path(os.environ.get('MEME_DATA', '/var/lib/meme-radar'))
DB = DATA / 'meme-radar.sqlite'
CAND_DIR = DATA / 'candidates_history'
BACKOFF_FILE = DATA / 'watch_backoff.json'
DEX = 'https://api.dexscreener.com'
CHAIN_IDS = ('bsc', 'solana', 'robinhood', 'xlayer', 'arc')
SLOTS = ((3600, '1h'), (21600, '6h'), (86400, '24h'))
SLOT_SECS = dict((name, secs) for secs, name in SLOTS)
BATCH = 30
# 时点窗口：到点后仍查不到价，宽限 6 小时继续重试；超窗则记一行 price_usd=NULL，
# 明示「该时点查不到价」，避免同批死链每 10 分钟被无限重试。
GRACE = 21600

DDL = '''CREATE TABLE IF NOT EXISTS candidate_watch_observations (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    observed TEXT NOT NULL,
    price_usd REAL,
    liquidity_usd REAL,
    slot TEXT NOT NULL,
    peak_price_usd REAL,
    first_seen TEXT,
    UNIQUE(chain, address, slot)
)'''
# a6d7 批：first_seen 记账字段。新行记锚点首见时刻（iso(info['at'])，原文照抄候选留痕）；
# 历史行留空，不倒推补填。
FIRST_SEEN_DDL = 'ALTER TABLE candidate_watch_observations ADD COLUMN first_seen TEXT'


def iso(ts):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))


def load_first_seen(now, max_age=2 * 86400):
    """取每个 (chain,address) 首次出现时刻/价；2 天内文件足够覆盖 24h 档。"""
    first = {}
    if not CAND_DIR.exists():
        return first
    for f in sorted(CAND_DIR.glob('*.jsonl')):
        try:
            if now - f.stat().st_mtime > max_age:
                continue
            for line in f.open(encoding='utf-8'):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                at = float(rec.get('at') or 0)
                if not at:
                    continue
                for it in rec.get('items') or ():
                    chain = (it.get('chain') or '').lower()
                    addr = str(it.get('address') or '').lower()
                    if not chain or not addr:
                        continue
                    k = (chain, addr)
                    if k not in first or at < first[k]['at']:
                        first[k] = {'at': at, 'price_usd': it.get('price_usd'),
                                    'liquidity_usd': it.get('liquidity_usd')}
        except Exception as e:
            print('候选池留痕读取失败 %s: %s' % (f.name, e))
    return first


def load_backoff(col):
    try:
        d = json.loads(BACKOFF_FILE.read_text())
        col.backoff = {k: float(v) for k, v in (d.get('backoff') or {}).items()}
        col.failures = {k: int(v) for k, v in (d.get('failures') or {}).items()}
    except Exception:
        pass


def save_backoff(col):
    tmp = BACKOFF_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps({'backoff': col.backoff, 'failures': col.failures}))
    tmp.replace(BACKOFF_FILE)


def fetch_batch(col, chain, addrs):
    """一批至多 30 地址；返回 {addr: (price, liquidity)}。失败按 Collector 退避。"""
    key = 'dexscreener:tokens:' + chain
    out = {}
    if col.backoff.get(key, 0) > time.time():
        return out, 'backoff'
    time.sleep(max(0, col.spacing - (time.monotonic() - col.last)))
    col.last = time.monotonic()
    try:
        data = get_json('%s/tokens/v1/%s/%s' % (DEX, chain, ','.join(addrs)), col.timeout)
        if not isinstance(data, list):
            raise FetchError('invalid_schema')
        for p in data:
            addr = str((p.get('baseToken') or {}).get('address') or '').lower()
            if addr not in addrs:
                continue
            try:
                price = float(p.get('priceUsd')) if p.get('priceUsd') not in (None, '') else None
            except (TypeError, ValueError):
                price = None
            liq = (p.get('liquidity') or {}).get('usd')
            prev = out.get(addr)
            if price is not None and (prev is None or (liq or 0) > (prev[1] or 0)):
                out[addr] = (price, float(liq) if liq is not None else None)
        col.failures[key] = 0
        col.backoff.pop(key, None)
    except Exception as e:
        code = e.code if isinstance(e, FetchError) else type(e).__name__
        n = col.failures.get(key, 0) + 1
        col.failures[key] = n
        suggested = getattr(e, 'retry_after', None)
        col.backoff[key] = time.time() + min(900, max(60 * 2 ** min(n - 1, 4), suggested or 0))
        return out, code
    return out, None


def main():
    now = time.time()
    db = sqlite3.connect(str(DB), timeout=30)
    db.execute(DDL)
    try:
        db.execute(FIRST_SEEN_DDL)
    except sqlite3.OperationalError as e:
        if 'duplicate column' not in str(e):
            raise
    db.commit()

    first = load_first_seen(now)
    if not first:
        print('候选池留痕为空，跳过')
        return

    have = {}
    for chain, addr, slot in db.execute('SELECT chain, address, slot FROM candidate_watch_observations'):
        have.setdefault((chain, addr), set()).add(slot)

    pending = {}
    anchors = 0
    for (chain, addr), info in first.items():
        if chain not in CHAIN_IDS:
            continue
        anchors += 1
        done = have.get((chain, addr), ())
        if 'first' not in done:
            pending.setdefault(chain, {}).setdefault(addr, []).append('first')
        elapsed = now - info['at']
        for secs, slot in SLOTS:
            if elapsed >= secs and slot not in done:
                pending.setdefault(chain, {}).setdefault(addr, []).append(slot)

    now_ts = now
    col = Collector(timeout=10, spacing=2.0)
    load_backoff(col)
    inserted = calls = errors = 0
    peak = {}
    for chain, addr, p in db.execute(
            'SELECT chain, address, max(coalesce(peak_price_usd, price_usd)) '
            'FROM candidate_watch_observations GROUP BY chain, address'):
        peak[(chain, addr)] = p

    for chain in sorted(pending):
        addrs = sorted(pending[chain])
        for i in range(0, len(addrs), BATCH):
            chunk = addrs[i:i + BATCH]
            calls += 1
            got, err = fetch_batch(col, chain, chunk)
            if err and err != 'backoff':
                errors += 1
                print('回查失败 %s: %s' % (chain, err))
            ts = iso(time.time())
            for addr in chunk:
                info = first[(chain, addr)]
                slots = pending[chain][addr]
                rows = []
                if 'first' in slots:
                    rows.append(('first', iso(info['at']), info['price_usd'],
                                 info['liquidity_usd']))
                if addr in got:
                    price, liq = got[addr]
                    rows.extend((s, ts, price, liq) for s in slots if s != 'first')
                else:
                    # 查不到价：宽限期内留待下轮；超窗则落 NULL 行，明示缺失
                    for s in slots:
                        if s == 'first':
                            continue
                        due = info['at'] + SLOT_SECS[s]
                        if now_ts - due >= GRACE:
                            rows.append((s, ts, None, None))
                    if not rows:
                        continue
                pk = peak.get((chain, addr))
                for slot, obs, price, liq in rows:
                    if price is not None:
                        pk = price if pk is None else max(pk, price)
                    db.execute(
                        'INSERT OR IGNORE INTO candidate_watch_observations'
                        '(chain,address,observed,price_usd,liquidity_usd,slot,peak_price_usd,first_seen)'
                        ' VALUES(?,?,?,?,?,?,?,?)',
                        (chain, addr, obs, price, liq, slot, pk, iso(info['at'])))
                    inserted += 1
        db.commit()
        save_backoff(col)

    now = time.time()
    have = {}
    nulls = set()
    for chain, addr, slot, price in db.execute(
            'SELECT chain, address, slot, price_usd FROM candidate_watch_observations'):
        have.setdefault((chain, addr), set()).add(slot)
        if price is None:
            nulls.add((chain, addr, slot))
    due1 = [k for k, v in first.items() if now - v['at'] >= 3600 and k[0] in CHAIN_IDS]
    got1 = sum(1 for k in due1 if '1h' in have.get(k, ()) and (k[0], k[1], '1h') not in nulls)
    miss1 = sum(1 for k in due1 if (k[0], k[1], '1h') in nulls)
    cov = (got1 / len(due1) * 100) if due1 else 0.0
    print('观察名单回查: 锚点 %d  本轮写入 %d 行  DexScreener 调用 %d 次  失败 %d'
          % (anchors, inserted, calls, errors))
    print('1h 覆盖率: %d/%d = %.1f%%（有效价；另有 %d 条超窗查不到价，已记 NULL）'
          % (got1, len(due1), cov, miss1))


if __name__ == '__main__':
    main()
