"""A/B 实验臂 H2 —— 独立台账，不混账(mark=ab_h2_v1)。

裁定：lead 2026-11-07T0945Z-8851 / home 2026-11-07T101606Z-78fa
  独立文件 paper_trade_ab.sqlite；不新增 timer/unit，由 paper_trade.py 同进程周期调用
  (实验臂与对照臂同一时刻取价)。
入场：liq $10–25K + 池龄 5–15 分 + 多源 ≥2 + 含 dex；出场规则与对照臂一致。
  池龄 = min(fetched_at) − created_at（H2 预登记第二节；lead 2026-11-07T1137Z-8c4e 确认修 bug 非规则变更）。
  多源 ≥2（lead 2026-11-07T1129Z 更正，撤回 110644Z-c6ae 的 ≥1 放宽）。首见查找窗口 30h，候选宇宙仍 24h。
判据：峰值≥10x 笔数 / 均值 / 归零率，跑满 150 笔再判；与 forward_tracks 分开统计。
2026-11-09 修正案（lead 134958Z-266f 批准，两条）：
  1) 开仓时刻池龄也必须 <=15 分钟（原条件只约束首见池龄，实际延迟中位 18.6 分钟、P90 19.5 小时）；
  2) 2026-11-09T13:43Z 之前开的 91 笔改为 mark=ab_h2_v0_stale，不计入 150 判据（入场价是陈价）。
"""
from __future__ import annotations
import json, sqlite3, time
from pathlib import Path

DATA = Path('/var/lib/meme-radar')
AB_DB = DATA / 'paper_trade_ab.sqlite'
RADAR_DB = DATA / 'meme-radar.sqlite'
ARM, MARK = 'H2', 'ab_h2_v1'
STOP_PCT, TP1, TP2, TRAIL_PCT, MAX_HOLD_DAYS = 0.50, 2.0, 5.0, 0.30, 7
# 2026-11-13 平行臂（用户批准 A/B 对比）：B 臂加「-30% 全部出掉」规则
EARLY_STOP = 0.70   # 首次跌破 0.70x（-30%）即全部出掉
MARK_B = 'ab_h2_v1_early30'
# 2026-11-14 C 臂：30 分钟时间出场。反事实扫描（对照臂 S/A 159 笔）：
# 持仓 30 分钟时价格中位仍在 1.00x，之后才衰减；30 分出 vs 实际出场 +20~25pp/受影响笔，
# 剔除 robinhood 冻结价后仍 +22pp。与 A/B 同一批候选、同一入场价，只差出场规则。
MARK_C = 'ab_h2_v1_time30'
TIME_EXIT_SEC = 1800
# 2026-11-21 D 臂：10 分钟时间出场（lead 2026-11-21T0335Z-lead-home-7e69-ack 批准纸面 A/B；参数锁定 10 分钟）。
# 同候选/同入场价/同成本，保留止损与移动止盈，只额外加「满 10 分钟全出」。
# 依据：C 臂 113 笔复算，10 分钟封顶净均值 -9.5%→+1.1%、中位 -43.9%→-23.0%，
# 配对 +10.7pp（CI +2.1~+19.2）；收益来自提前避开 10 分钟后才触发的止损/止盈。
MARK_D = 'ab_h2_v1_time10'
TIME_EXIT_SEC_D = 600
AB_LIQ_MIN, AB_LIQ_MAX, AB_AGE_MIN, AB_AGE_MAX = 10000, 25000, 5, 15
LOOKBACK_H = 24
FIRST_SEEN_H = 30


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS positions(
        key TEXT PRIMARY KEY, arm TEXT, mark TEXT, symbol TEXT, chain TEXT, address TEXT,
        entry REAL, opened REAL, peak REAL, stop REAL,
        tp1_done INT DEFAULT 0, tp2_done INT DEFAULT 0, closed INT DEFAULT 0,
        realized REAL DEFAULT 0, last_price REAL, last_at REAL, note TEXT,
        closed_at REAL, liquidity_usd REAL, market_cap REAL, score REAL,
        onchain_liq REAL, pair_address TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS fills(
        key TEXT, arm TEXT, at REAL, kind TEXT, price REAL, mult REAL, pnl REAL)''')
    try:
        db.execute('ALTER TABLE positions ADD COLUMN onchain_liq REAL')
    except Exception:
        pass
    try:
        db.execute('ALTER TABLE positions ADD COLUMN pair_address TEXT')
    except Exception:
        pass
    db.execute('CREATE INDEX IF NOT EXISTS ab_fills_by_key ON fills(key, at)')
    db.execute('''CREATE TABLE IF NOT EXISTS price_marks(
        key TEXT, arm TEXT, at REAL, price REAL, mult REAL)''')
    db.execute('CREATE INDEX IF NOT EXISTS ab_marks_by_key ON price_marks(key, at)')


def _token_meta(db, now, lookback_h=LOOKBACK_H):
    """按标的聚合：首见 fetched_at、source 集合、是否出现过 dex。多源与含 dex 按标的判（单行 payload 只有单个 source）。"""
    """H2 预登记：池龄 = 该标的 signals 里最早一条 fetched_at − 其 created_at。
    首见查找窗口单独放宽到 30h（lead 2026-11-07T1137Z-8c4e 批准），candidates 宇宙仍 24h。"""
    ix = {}; src = {}; dex = set()
    for _c, payload in db.execute('SELECT created, payload FROM signals WHERE created >= ?',
                                  (now - max(lookback_h, FIRST_SEEN_H) * 3600,)):
        try:
            d = json.loads(payload)
        except Exception:
            continue
        k = (d.get('chain'), str(d.get('address', '')).lower())
        ft = d.get('fetched_at')
        if ft is None:
            continue
        try:
            ft = float(ft)
        except (TypeError, ValueError):
            continue
        if k not in ix or ft < ix[k]:
            ix[k] = ft
        if d.get('source'):
            src.setdefault(k, set()).add(str(d.get('source')))
        if d.get('dex'):
            dex.add(k)
    return ix, src, dex


def _age_min(d, ix):
    """池龄（分钟）= min(fetched_at) − created_at，符合 H2 预登记第二节。"""
    if not ix:
        return None
    ft = ix.get((d.get('chain'), str(d.get('address', '')).lower()))
    c = d.get('created_at')
    if ft is None or not c:
        return None
    c = float(c); c = c / 1000 if c > 1e10 else c
    return max(0.0, (ft - c) / 60)


def _sources(d):
    s = set()
    for x in (d.get('mention_sources') or []):
        s.add('m:' + str(x)[:60])
    for x in (d.get('smart_wallet_sources') or []):
        s.add('w:' + str(x)[:60])
    if (d.get('x_hits') or 0) > 0: s.add('x')
    if (d.get('kol_hits') or 0) > 0: s.add('k')
    if (d.get('smart_wallet_hits') or 0) > 0: s.add('s')
    return s


def candidates(now):
    """2026-11-14 方案 A′（修正版）：按「开仓时点池龄 5–15 分」选候选。

    为什么改（实测，不是推测）：
      · 原预登记用「首见池龄 5–15 分」，那是旧管道下买不到新池时的代理指标。
      · 11-09 修正案又加了「开仓时池龄 ≤15 分」，理由是「首见→开仓延迟中位 18.6 分钟」。
      · 11-14 复核：signals 插入延迟中位 0.2 分钟、band 内首见池龄中位 2.3 分钟 —— 管道不慢。
        真正把候选卡死的是两条年龄门槛的交集：24h 内 2,670 条 band 行里只有 1 条同时满足，
        实测 0.97 笔/天，A/B 臂几乎开不出仓。
      · 现改为直接按池子真实年龄判：开仓时点 5 ≤ (now − created_at)/60 ≤ 15。
        24h 实测产量：band 内、15 分钟内攒到 ≥2 个来源的独立标的 31 个/天。
    多源 ≥2 与 dex 门槛不变（lead 2026-11-07T1129Z）；liq $10–25K 不变。
    """
    out = {}
    try:
        db = sqlite3.connect(f'file:{RADAR_DB}?mode=ro', uri=True, timeout=15)
        rows = db.execute('SELECT created, payload FROM signals WHERE created >= ?',
                          (now - LOOKBACK_H * 3600,)).fetchall()
        ix, src, dex = _token_meta(db, now)
        db.close()
    except Exception as e:
        print(f'AB臂: 读 signals 失败 {e}')
        return []
    for _c, payload in rows:
        try:
            d = json.loads(payload)
        except Exception:
            continue
        liq = d.get('liquidity_usd') or 0
        if not (AB_LIQ_MIN <= liq <= AB_LIQ_MAX): continue
        c = d.get('created_at')
        if not c: continue
        try:
            c = float(c)
            if c > 1e14: c /= 1e6          # 微秒（少数源给 16 位）
            elif c > 1e10: c /= 1000       # 毫秒
        except (TypeError, ValueError):
            continue
        age_min = (now - c) / 60
        if not (AB_AGE_MIN <= age_min <= AB_AGE_MAX): continue
        _k = (d.get('chain'), str(d.get('address', '')).lower())
        if len(src.get(_k, set()) | _sources(d)) < 2: continue
        if not d.get('dex') and _k not in dex: continue
        if not d.get('address') or not (d.get('price_usd') or 0): continue
        d = dict(d)
        d['age_minutes'] = round(age_min, 1)
        # 同一标的 24h 内可能有多行（不同源/不同时刻）：只留 fetched_at 最新的一行，
        # 否则开仓会拿到最老那一行的陈价（2026-11-14 修）。
        prev = out.get(_k)
        if prev is None or (d.get('fetched_at') or 0) > (prev.get('fetched_at') or 0):
            out[_k] = d
    return list(out.values())


def price_of(c, matches, jup=None):
    """与对照臂同口径取价：优先最新扫描 price_usd，掉出候选走 DexScreener 兜底。"""
    if c.get('chain') == 'solana' and jup is not None:
        a = str(c.get('address', ''))
        v = jup.get(a)
        if v:
            return v
    # 2026-11-14：候选行自带的 price_usd 是扫描时点的价（插入延迟中位 0.2 分钟），
    # 比 matches.json（每小时覆盖写、只有 S/A）新；band 候选是 B 级，本来就查不到 matches。
    if c.get('price_usd') and c.get('fetched_at'):
        try:
            if (time.time() - float(c['fetched_at'])) / 60 <= 20:
                return float(c['price_usd'])
        except (TypeError, ValueError):
            pass
    row = matches.get((c.get('chain'), str(c.get('address', '')).lower()))
    if row and row.get('price_usd'):
        try:
            return float(row['price_usd'])
        except (TypeError, ValueError):
            pass
    try:
        import sources
        url = f"{sources.DEX}/tokens/v1/{c.get('chain')}/{c.get('address')}"
        data = sources.get_json(url, 10)
        if isinstance(data, list) and data:
            best = max(data, key=lambda r: float((r.get('liquidity') or {}).get('usd') or 0))
            v = float(best.get('priceUsd') or 0)
            return v or None
    except Exception:
        pass
    return None


BSC_RPC = 'https://bsc-dataseed.binance.org'
_WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
_USDT = '0x55d398326f99059ff775485246999027b3197955'


def _rpc(to, data, timeout=6):
    import urllib.request
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_call',
                       'params': [{'to': to, 'data': data}, 'latest']}).encode()
    req = urllib.request.Request(BSC_RPC, data=body,
                                 headers={'User-Agent': 'meme-radar/0.2',
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get('result')


JUP_API = 'https://lite-api.jup.ag'


def jupiter_prices(addrs, timeout=12):
    """solana 批量取价（Jupiter /price/v3，一次最多 50 个）。

    2026-11-11：A/B 臂原来对 solana 也走 DexScreener /tokens/v1，而那个端点对该链
    会给错价（实测某币 9.185e-06 → 334.57，1h/6h 逐位相同，3600 万倍伪影）。
    已在 watch_observe 切 Jupiter，这里同步。
    返回 {address: price|None}；None 表示该币在 Jupiter 眼里已无价（死币）。
    """
    import urllib.request
    out = {}
    for i in range(0, len(addrs), 50):
        chunk = addrs[i:i + 50]
        try:
            url = '%s/price/v3?ids=%s' % (JUP_API, ','.join(chunk))
            req = urllib.request.Request(url, headers={
                'User-Agent': 'meme-radar/0.2', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            if not isinstance(data, dict):
                continue
            want = dict((a.lower(), a) for a in chunk)
            for k, v in data.items():
                a = want.get(str(k).lower())
                if a is None:
                    continue
                p = v.get('usdPrice')
                try:
                    out[a] = float(p) if p not in (None, '') else None
                except (TypeError, ValueError):
                    out[a] = None
        except Exception:
            pass
    return out


def onchain_liq_usd(pair_address, quote_price_usd=690.0):
    """链上直读 pool 储备算美元流动性（只对 Uniswap-V2 风格池有效）。

    2026-11-10：扫描侧的 liquidity_usd 是**扫描那一刻**的读数，而币可能在几分钟内被抽干。
    这里记录开仓时刻链上真实储备，用来事后检验「入场时池子还在不在」。
    只记录、不作为开仓条件。
    只依赖报价币（WBNB/USDT）的 18 位小数，不需要代币本身的 decimals。
    """
    if not pair_address:
        return None
    try:
        h = _rpc(pair_address, '0x0902f1ac')            # getReserves()
        if not h or h == '0x' or len(h) < 130:
            return None
        x = h[2:]
        r0 = int(x[0:64], 16)
        r1 = int(x[64:128], 16)
        t0 = str(_rpc(pair_address, '0x0dfe1681') or '')[-40:].lower()   # token0()
        t1 = str(_rpc(pair_address, '0xd21220a7') or '')[-40:].lower()   # token1()
        if t0 == _WBNB[2:]:
            return r0 / 1e18 * quote_price_usd * 2
        if t1 == _WBNB[2:]:
            return r1 / 1e18 * quote_price_usd * 2
        if t0 == _USDT[2:]:
            return r0 / 1e18 * 2
        if t1 == _USDT[2:]:
            return r1 / 1e18 * 2
    except Exception:
        pass
    return None


def run(matches, now=None, verbose=True):
    now = now or time.time()
    db = sqlite3.connect(str(AB_DB), timeout=15)
    db.row_factory = sqlite3.Row
    init(db)
    opened = closed = 0
    # 2026-11-11：solana 的持仓价统一走 Jupiter（DexScreener 对该链不可用）
    _cands = candidates(now)
    _jup = {}
    try:
        _sol = [r['address'] for r in db.execute(
            "SELECT address FROM positions WHERE closed=0 AND chain='solana'")]
        _sol += [d.get('address') for d in _cands if str(d.get('chain')) == 'solana']
        _sol = [a for a in dict.fromkeys(_sol) if a]
        if _sol:
            _jup = jupiter_prices(_sol)
    except Exception:
        _jup = {}
    def open_positions(mark):
        opened_n = 0
        for d in _cands:
            key = f"H2|{mark}|{d.get('chain')}:{str(d.get('address', '')).lower()}"
            prev = db.execute('SELECT closed, closed_at FROM positions WHERE key=?', (key,)).fetchone()
            if prev is not None:
                if not prev['closed']:
                    continue
                if prev['closed_at'] is not None and now - prev['closed_at'] < 24 * 3600:
                    continue
                db.execute('DELETE FROM positions WHERE key=?', (key,))
            # 2026-11-09 修 bug：原用 signals 里最老一条的 price_usd 当作入场价，
            # 改为与对照臂同口径：开仓时刻的市价。
            entry = price_of(d, matches, _jup)
            if not entry or entry <= 0:
                continue
            _pair = d.get('pair_address')
            _ocl = onchain_liq_usd(_pair) if str(d.get('chain')) == 'bsc' else None
            db.execute('INSERT INTO positions(key,arm,mark,symbol,chain,address,entry,opened,peak,stop,'
                       'last_price,last_at,liquidity_usd,market_cap,score,onchain_liq,pair_address)'
                       ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (key, ARM, mark, d.get('symbol'), d.get('chain'), d.get('address'),
                        entry, now, entry, entry * (1 - STOP_PCT), entry, now,
                        d.get('liquidity_usd'), d.get('market_cap'),
                        (d.get('score_meta') or {}).get('score'), _ocl, _pair))
            opened_n += 1
        return opened_n

    opened = open_positions(MARK)
    opened_b = open_positions(MARK_B)
    opened_c = open_positions(MARK_C)
    opened_d = open_positions(MARK_D)
    for p in db.execute('SELECT * FROM positions WHERE closed=0').fetchall():
        is_b = (p['mark'] == MARK_B)
        is_c = (p['mark'] == MARK_C)
        is_d = (p['mark'] == MARK_D)
        entry = p['entry'] or 0
        # 2026-11-11：Jupiter 认识这个币但没有 usdPrice = 流动性≈0，已死透。
        # 原来会一直挂到 7 天超时才平，且用死前的 last_price 结算 —— 低估亏损。
        # 现在立即按剩余仓位全损平仓。
        # 宽限 30 分钟：刚开仓的新币 Jupiter 可能还没索引到，不能立刻判死
        if (str(p['chain']) == 'solana' and p['address'] in _jup
                and _jup[p['address']] is None
                and now - (p['opened'] or now) >= 1800):
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0:
                db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?)',
                           (p['key'], ARM, now, 'dead', 0.0, 0.0, -left))
                db.execute('UPDATE positions SET realized=realized+? WHERE key=?',
                           (-left, p['key']))
            db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                       (now, '归零(无价)', p['key']))
            closed += 1
            continue
        price = price_of({'chain': p['chain'], 'address': p['address']}, matches, _jup)
        if not price:
            if entry > 0 and now - p['opened'] >= MAX_HOLD_DAYS * 86400:
                last = p['last_price'] or entry
                left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
                if left > 0 and last:
                    m = last / entry
                    db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?)',
                               (p['key'], ARM, now, 'timeout_nopx', last, m, left * (m - 1)))
                    db.execute('UPDATE positions SET realized=realized+? WHERE key=?',
                               (left * (m - 1), p['key']))
                db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                           (now, '持有到期(无行情)', p['key']))
                closed += 1
            continue
        if entry <= 0: continue
        peak = max(p['peak'], price)
        db.execute('UPDATE positions SET peak=?, last_price=?, last_at=? WHERE key=?',
                   (peak, price, now, p['key']))
        mult = price / entry
        db.execute('INSERT INTO price_marks VALUES(?,?,?,?,?)', (p['key'], ARM, now, price, mult))
        def close_part(kind, frac, px):
            m = px / entry; pnl = frac * (m - 1)
            db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?)',
                       (p['key'], ARM, now, kind, px, m, pnl))
            db.execute('UPDATE positions SET realized=realized+? WHERE key=?', (pnl, p['key']))

        # 2026-11-14 修 bug：close_part 原来定义在 B 臂规则**之后**，
        # B 臂第一次触发就会 UnboundLocalError（run() 整体抛错、整轮不处理）。
        # 定义已提到所有调用之前。
        # 2026-11-13 B 臂专属：首次跌破 -30% 即全部出掉（用户批准的 A/B 对比）
        if is_b and price <= entry * EARLY_STOP:
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0:
                close_part('early30', left, price)
            db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                       (now, '预警-30%出场', p['key']))
            closed += 1; continue

        # 2026-11-14 C 臂：持仓满 30 分钟，在第一个 ≥30 分钟的轮次按现价全出
        if is_c and now - (p['opened'] or now) >= TIME_EXIT_SEC:
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0:
                close_part('time30', left, price)
            db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                       (now, '时间出场30分', p['key']))
            closed += 1; continue

        # 2026-11-21 D 臂：持仓满 10 分钟，在第一个 ≥10 分钟的轮次按现价全出。
        # 与 C 臂同位置（止损之前）：取不到价的仓位走不到这里，仍在 30 分钟宽限后
        # 按归零处理——10 分钟规则无法卖出没有报价的币，这是诚实口径。
        if is_d and now - (p['opened'] or now) >= TIME_EXIT_SEC_D:
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0:
                close_part('time10', left, price)
            db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                       (now, '时间出场10分', p['key']))
            closed += 1; continue

        if price <= p['stop']:
            close_part('stop', 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0, price)
            db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                       (now, '止损', p['key']))
            closed += 1; continue
        if not p['tp1_done'] and mult >= TP1:
            close_part('tp1', 1/3, entry * TP1)
            db.execute('UPDATE positions SET tp1_done=1 WHERE key=?', (p['key'],))
        if not p['tp2_done'] and mult >= TP2:
            close_part('tp2', 1/3, entry * TP2)
            db.execute('UPDATE positions SET tp2_done=1 WHERE key=?', (p['key'],))
        if peak > entry and price <= peak * (1 - TRAIL_PCT):
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0:
                close_part('trail', left, price)
                db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                           (now, '移动止盈', p['key']))
                closed += 1; continue
        if now - p['opened'] >= MAX_HOLD_DAYS * 86400:
            left = 1.0 - (int(p['tp1_done']) + int(p['tp2_done'])) / 3.0
            if left > 0: close_part('timeout', left, price)
            db.execute("UPDATE positions SET closed=1, closed_at=?, note=? WHERE key=?",
                       (now, '持有到期', p['key']))
            closed += 1
    db.commit()
    if verbose:
        tot = db.execute('SELECT COUNT(*) FROM positions').fetchone()[0]
        print(f'实验臂 H2({MARK}): 新开仓 {opened} | 平仓 {closed} | 台账共 {tot} 笔')
        print(f'  B 臂新开仓 {opened_b} | C 臂新开仓 {opened_c} | D 臂新开仓 {opened_d}')
    db.close()
    return opened, closed


def report():
    db = sqlite3.connect(str(AB_DB), timeout=15)
    db.row_factory = sqlite3.Row
    init(db)
    rows = db.execute('SELECT * FROM positions WHERE mark=?', (MARK,)).fetchall()
    # 只数「陈价/旧口径」遗留；B/C 平行臂不算 stale（2026-11-14 修）
    stale = db.execute(
        "SELECT COUNT(*) FROM positions WHERE mark<>? AND (mark LIKE 'ab_h2_v0%' OR mark LIKE '%_oldpop')",
        (MARK,)).fetchone()[0]
    closed = [r for r in rows if r['closed']]
    print('=' * 72)
    print(f'A/B 实验臂 H2({MARK}): 共 {len(rows)} 笔 | 已平仓 {len(closed)} | 持仓中 {len(rows)-len(closed)}')
    if stale:
        print(f'  （另有 {stale} 笔陈价/旧口径遗留，不计入 150 判据，仅作参考）')
    if closed:
        v = sorted(r['realized'] for r in closed)
        ge10 = sum(1 for r in closed if r['entry'] and (r['peak'] / r['entry']) >= 10)
        zero = sum(1 for r in v if r <= -0.99)
        print(f'  均值 {sum(v)/len(v)*100:+.1f}% | 中位 {v[len(v)//2]*100:+.1f}%'
              f' | 峰值≥10x {ge10} 笔 | 归零(≤-99%) {zero} 笔')
    print(f'  距判据 150 笔还差 {max(0, 150-len(closed))} 笔已平仓')
    rows_b = db.execute('SELECT * FROM positions WHERE mark=?', (MARK_B,)).fetchall()
    cl_b=[r['realized'] for r in rows_b if r['closed'] and r['realized'] is not None]
    print(f'  B 臂({MARK_B}): 共 {len(rows_b)} 笔 | 已平 {len(cl_b)}')
    rows_c = db.execute('SELECT * FROM positions WHERE mark=?', (MARK_C,)).fetchall()
    cl_c = [r['realized'] for r in rows_c if r['closed'] and r['realized'] is not None]
    print(f'  C 臂({MARK_C}): 共 {len(rows_c)} 笔 | 已平 {len(cl_c)}')
    rows_d = db.execute('SELECT * FROM positions WHERE mark=?', (MARK_D,)).fetchall()
    cl_d = [r['realized'] for r in rows_d if r['closed'] and r['realized'] is not None]
    print(f'  D 臂({MARK_D}): 共 {len(rows_d)} 笔 | 已平 {len(cl_d)}')
    for _lbl, _cl in (('B', cl_b), ('C', cl_c), ('D', cl_d)):
        if _cl:
            vv = sorted(_cl)
            print(f'    {_lbl} 均值 {sum(_cl)/len(_cl)*100:+.1f}% | 中位 {vv[len(_cl)//2]*100:+.1f}%'
                  f' | 归零 {sum(1 for x in _cl if x<=-0.99)} 笔')
    db.close()


if __name__ == '__main__':
    import sys
    if '--report' in sys.argv:
        report()
    else:
        print('实验臂请通过 paper_trade.py --ab 调用（同进程取价）')
