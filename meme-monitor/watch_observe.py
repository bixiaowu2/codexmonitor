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
# 74bc 批（2026-11-02 lead d2a9 批准）：锚点池来源开关。
# history（默认）= candidates_history/*.jsonl 留痕；archive = candidates_archive.first_seen。
# 回滚办法：改回 history 或删掉该变量，下一轮即恢复原口径。
SOURCE = (os.environ.get('MEME_WATCH_SOURCE') or 'history').strip().lower()
if SOURCE not in ('history', 'archive'):
    raise SystemExit('MEME_WATCH_SOURCE 非法: %r（只接受 history|archive）' % SOURCE)
# 24h 档 + 6h 宽限 = 30h，再留 1h 余量。原来只取 25h，导致 24h 档查不到价的标的
# 在宽限期内就被踢出锚点池，连 NULL 行都写不下（实测 11-04 的 24h 覆盖率只剩 45%）。
ARCHIVE_MAX_AGE = 86400 + 21600 + 3600
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
# 2026-11-03 fa79 批：due_at 记账字段。记该时点的应采时刻
# （first_seen + 档位秒数），observed 仍为实际取价时刻。
# 分析侧可用 observed - due_at 算偏差并过滤，不再靠时间戳猜。
# 历史行留空，不倒推补填。
DUE_AT_DDL = 'ALTER TABLE candidate_watch_observations ADD COLUMN due_at TEXT'


def iso(ts):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))


def load_first_seen(now, max_age=2 * 86400):
    """取每个 (chain,address) 首次出现时刻/价；按 SOURCE 选池。

    history: 读 candidates_history/*.jsonl（2 天内文件足够覆盖 24h 档）。
    archive: 读 candidates_archive，first_seen 取该 (chain,address) 的 min，
             price/liquidity 取该最早 scanned_at 行的 entry_price/liquidity。
    """
    first = {}
    if SOURCE == 'archive':
        return load_first_seen_archive(now)
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
                    # Solana 地址 base58 大小写敏感（2026-11-02 lead c3e2fwd）：
                    # 保留原文大小写，不再 lower()，否则身份键与归档/signals 无法精确 join
                    addr = str(it.get('address') or '').strip()
                    if not chain or not addr:
                        continue
                    k = (chain, addr)
                    if k not in first or at < first[k]['at']:
                        first[k] = {'at': at, 'price_usd': it.get('price_usd'),
                                    'liquidity_usd': it.get('liquidity_usd')}
        except Exception as e:
            print('候选池留痕读取失败 %s: %s' % (f.name, e))
    return first


def load_first_seen_archive(now):
    """从 candidates_archive 取锚点（74bc）。只读，不写归档表。

    first_seen 为该标的在归档表里的最小首见时刻；价格取对应最早一行的
    entry_price（无则 NULL），流动性取同一行 liquidity。原文照抄，不改写。
    """
    first = {}
    cutoff = now - ARCHIVE_MAX_AGE
    db = sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=30)
    try:
        rows = db.execute(
            'SELECT chain, address, first_seen, entry_price, liquidity '
            'FROM candidates_archive WHERE first_seen IS NOT NULL '
            'AND first_seen >= ? '
            'ORDER BY chain, address, first_seen ASC, scanned_at ASC',
            (cutoff,))
        for chain, addr, fs, price, liq in rows:
            chain = (chain or '').lower()
            # 同上：归档里的 solana 地址是 base58 原文，不得 lower()
            addr = str(addr or '').strip()
            if not chain or not addr:
                continue
            k = (chain, addr)
            if k in first:
                continue  # 已按 first_seen 升序，首行即最早的
            try:
                at = float(fs)
            except (TypeError, ValueError):
                continue
            first[k] = {'at': at, 'price_usd': price, 'liquidity_usd': liq}
    finally:
        db.close()
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
    # 请求侧 addrs 为 base58 原文；接口回传也应是原文。仍按大小写不敏感建索引，
    # 只为容错，但返回值一律用请求侧的原始大小写做键，避免身份键再被写漂。
    want = dict((a.lower(), a) for a in addrs)
    try:
        data = get_json('%s/tokens/v1/%s/%s' % (DEX, chain, ','.join(addrs)), col.timeout)
        if not isinstance(data, list):
            raise FetchError('invalid_schema')
        for p in data:
            raw = str((p.get('baseToken') or {}).get('address') or '').strip()
            addr = want.get(raw.lower())
            if addr is None:
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
    try:
        db.execute(DUE_AT_DDL)
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
                # 2026-11-03 fa79 批：多档同时逾期（进程停摆/长退避后恢复）时，
                # 只补最近一档；更早的档位缺就是缺，不再用当前价冒充历史时点。
                due_slots = [s for s in slots if s != 'first']
                if len(due_slots) > 1:
                    due_slots = [max(due_slots, key=lambda x: SLOT_SECS[x])]
                rows = []
                if 'first' in slots:
                    rows.append(('first', iso(info['at']), info['price_usd'],
                                 info['liquidity_usd'], iso(info['at'])))
                if addr in got:
                    price, liq = got[addr]
                    rows.extend((s, ts, price, liq,
                                 iso(info['at'] + SLOT_SECS[s]))
                                for s in due_slots)
                else:
                    # 查不到价：宽限期内留待下轮；超窗则落 NULL 行，明示缺失
                    for s in due_slots:
                        due = info['at'] + SLOT_SECS[s]
                        if now_ts - due >= GRACE:
                            rows.append((s, ts, None, None,
                                         iso(info['at'] + SLOT_SECS[s])))
                    if not rows:
                        continue
                pk = peak.get((chain, addr))
                for slot, obs, price, liq, due_at in rows:
                    if price is not None:
                        pk = price if pk is None else max(pk, price)
                    db.execute(
                        'INSERT OR IGNORE INTO candidate_watch_observations'
                        '(chain,address,observed,price_usd,liquidity_usd,slot,peak_price_usd,first_seen,due_at)'
                        ' VALUES(?,?,?,?,?,?,?,?,?)',
                        (chain, addr, obs, price, liq, slot, pk, iso(info['at']), due_at))
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
