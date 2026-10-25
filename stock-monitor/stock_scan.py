#!/usr/bin/env python3
"""股票筛选器(统一妖币逻辑)。对 stock-radar 候选分 S/A/B 级。

S级筛选: stock-radar score 高 + akshare 十大流通股东里"大股东集中度高"
(剔除 HKSCC/基金/国资后的前几大流通股东占比高, 类似妖币的"主力控盘")。
信号(用价格): 启动(单日大涨≥5%) / 回撤(≥10%) / 分批止盈(+50%/100%)。
所有推送 TG + 钉钉。akshare 需 /opt/akshare-venv。
"""
from __future__ import annotations
import sys, json, time, types, os
from pathlib import Path
sys.path.insert(0, '/opt/stock-radar')
from notify import send_channel
from markets import market_of, MARKET_ORDER

sys.path.insert(0, '/opt/akshare-venv/lib/python3.12/site-packages')
import akshare as ak
import yfinance as yf

DATA = Path('/var/lib/stock-radar')
MATCHES = DATA / 'stock_matches.json'
STATE = DATA / 'scan_state.json'


# ---------- 钉钉降频 ----------
# 钉钉自定义机器人有企业级月配额(实测 errcode 90030 会耗尽), 而这几套雷达每轮都直发。
# 故把钉钉改为"入缓冲 + 每 DT_INTERVAL 汇总一条"; Telegram 不受影响, 仍即时。
DT_INTERVAL = 21600
DT_BUFFER_MAX = 30
_DT_STATE = DATA / 'dt_digest.json'


def _dt_load():
    try:
        return json.loads(_DT_STATE.read_text())
    except Exception:
        return {}


def _dt_save(d):
    try:
        _DT_STATE.write_text(json.dumps(d))
    except Exception:
        pass
AK_DIR = DATA / 'akshare_cache'


def secret(env, name):
    p = env.get(name + '_FILE')
    return Path(p).read_text().strip() if p else env.get(name, '').strip()


def make_cfg(env):
    return types.SimpleNamespace(
        telegram_enabled=env.get('STOCK_TELEGRAM_ENABLED', 'false').strip().lower() == 'true',
        telegram_token=secret(env, 'STOCK_TELEGRAM_BOT_TOKEN'),
        telegram_chat=secret(env, 'STOCK_TELEGRAM_CHAT_ID'),
        dingtalk_enabled=env.get('STOCK_DINGTALK_ENABLED', 'false').strip().lower() == 'true',
        dingtalk_webhook=secret(env, 'STOCK_DINGTALK_WEBHOOK'),
        dingtalk_secret=secret(env, 'STOCK_DINGTALK_SECRET'),
        dingtalk_keyword=env.get('STOCK_DINGTALK_KEYWORD', 'stock').strip(),
        timeout=10,
    )


def notify(env, body):
    """Telegram 即时; 钉钉走缓冲降频(见文件头说明)。"""
    cfg = make_cfg(env)
    sent = False
    if cfg.telegram_enabled:
        try:
            send_channel(cfg, body, 'telegram'); print('telegram: sent'); sent = True
        except Exception as e:
            print('tg fail:', str(e)[:100])
    if cfg.dingtalk_enabled:
        d = _dt_load()
        buf = (d.get('buf') or []) + [body]
        if time.time() - d.get('last', 0) < DT_INTERVAL:
            d['buf'] = buf[-DT_BUFFER_MAX:]
            _dt_save(d)
            print(f'dingtalk: 缓冲 {len(buf)} 条, 未到发送窗口')
            return sent
        d['last'] = time.time()   # 无论成败都重置, 否则配额耗尽时每轮重试
        try:
            send_channel(cfg, '\n\n'.join(buf), 'dingtalk')
            print(f'dingtalk: sent (汇总 {len(buf)} 条)'); sent = True
            d['buf'] = []
        except Exception as e:
            print('dt fail:', str(e)[:100])
            d['buf'] = buf[-DT_BUFFER_MAX:]
        _dt_save(d)
    return sent
def get_candidates():
    try:
        import sqlite3
        c = sqlite3.connect(str(DATA / 'stock-radar.sqlite'))
        r = c.execute("SELECT value FROM runtime WHERE key='latest'").fetchone()
        if not r:
            return []
        return json.loads(r[0]).get('rows', [])
    except Exception:
        return []


def big_holder_concentration(symbol):
    """akshare 十大流通股东, 剔除 HKSCC/基金/理财/国资后, 算前5大真实大股东占比。"""
    # A股代码: 688981.SS -> SH688981, 002371.SZ -> SZ002371
    code = symbol.split('.')[0]
    prefix = 'SH' if symbol.endswith('.SS') else ('SZ' if symbol.endswith('.SZ') else None)
    if not prefix:
        return None  # 非A股(美股/港股), akshare股东接口不适用
    sym = prefix + code
    cache = AK_DIR / (sym + '.json')
    now = time.time()
    if cache.exists() and now - cache.stat().st_mtime < 86400 * 7:  # 股东数据7天缓存
        try:
            return json.loads(cache.read_text()).get('conc')
        except Exception:
            pass
    try:
        df = ak.stock_gdfx_free_top_10_em(symbol=sym)
        exclude_kw = ['HKSCC', 'NOMINEE', '基金', '理财', '证券', '资产管理', '社保基金', 'QFII', '保险']
        real = []
        for _, row in df.iterrows():
            name = str(row.get('股东名称', ''))
            if any(k in name for k in exclude_kw):
                continue
            pct = row.get('占总流通股本持股比例')
            try:
                pct = float(pct)
            except (TypeError, ValueError):
                continue
            real.append(pct)
        conc = sum(sorted(real, reverse=True)[:5])  # 前5大真实大股东占比(百分数)
        AK_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({'conc': conc, 'ts': now}))
        return conc
    except Exception:
        return None


def yf_concentration(symbol):
    cache = AK_DIR / (symbol.replace('.','_') + '.json')
    now = time.time()
    if cache.exists() and now - cache.stat().st_mtime < 86400*7:
        try:
            return json.loads(cache.read_text()).get('conc')
        except Exception:
            pass
    try:
        t = yf.Ticker(symbol)
        mh = t.major_holders
        if mh is None or len(mh)==0:
            return None
        # yfinance 新版 major_holders 是单列表(index=标签, Value=值)
        d = {str(k): v for k, v in mh['Value'].items()}
        conc = (float(d.get('insidersPercentHeld',0) or 0) + float(d.get('institutionsPercentHeld',0) or 0)) * 100
        AK_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({'conc': conc, 'ts': now}))
        return conc
    except Exception:
        return None

def main():
    env = dict(os.environ)
    cands = get_candidates()
    if not cands:
        print('无股票候选, 跳过')
        return
    for c in cands:
        s = c.get('score_meta', {}).get('score', 0) if isinstance(c.get('score_meta'), dict) else 0
        c['_score'] = s
        sym = c.get('symbol', '')
        c['_conc'] = big_holder_concentration(sym)
        if c['_conc'] is None:
            c['_conc'] = yf_concentration(sym)
    # 分级: 大股东集中度高(≥30%) 且 score 高 = S; 或 score 极高
    # 注意: 这里的"集中度高 = 利好"只适用于**上市公司股权**(大股东控盘=筹码锁定)。
    # meme 那边(safety.py)把高集中度判为**风险** —— 因为 DEX 代币的高控盘 = 随时砸盘。
    # 两者不矛盾, 是标的性质不同; 不要为了"统一"而改掉任一侧。
    for c in cands:
        s = c['_score']
        conc = c['_conc']
        if conc is not None and conc >= 30 and s >= 50:
            c['_tier'] = 'S'
        elif (conc is not None and conc >= 20) or s >= 45:
            c['_tier'] = 'A'
        else:
            c['_tier'] = 'B'
    def mkt_of(c):
        sym = c.get('symbol', '')
        if sym.endswith('.SS') or sym.endswith('.SZ'):
            return 'A股'
        if sym.endswith('.HK'):
            return '港股'
        if '.KS' in sym:
            return '韩股'
        return '美股'
    for c in cands:
        c['_mkt'] = mkt_of(c)
    sa = [c for c in cands if c['_tier'] in ('S', 'A')]
    sa.sort(key=lambda c: (-(c['_conc'] if c['_conc'] is not None else -1), -(c['_score'] or 0)))
    s_tier = [{'symbol': c.get('symbol'), 'name': c.get('name'), 'conc': c['_conc'], 'tier': 'S'} for c in sa if c['_tier'] == 'S']
    MATCHES.write_text(json.dumps(s_tier, ensure_ascii=False, indent=2))
    # 2026-10-25 补度量: 每天归档一份 S 级名单。stock_matches.json 每天被覆盖、只留一份 .prev.json,
    # 于是「S 级是否比非 S 级好」只能用**今天的名单**去回溯评估 → 前视污染。
    # 追加归档(只增不改), 攒够若干天后可做时间正确的对比。失败不影响扫描。
    try:
        with (DATA / 'stock_matches_history.jsonl').open('a', encoding='utf-8') as _af:
            _af.write(json.dumps({'at': time.time(),
                                  'symbols': [x['symbol'] for x in s_tier]},
                                 ensure_ascii=False) + '\n')
    except Exception:
        pass

    ts = time.strftime('%m-%d %H:%M UTC', time.gmtime())
    prev = {}
    pf = MATCHES.with_suffix('.prev.json')
    if pf.exists():
        try:
            prev = {x['symbol']: x for x in json.loads(pf.read_text())}
        except Exception:
            pass
    cur = {x['symbol']: x for x in s_tier}
    new = [s for s in cur if s not in prev]
    gone = [s for s in prev if s not in cur]
    pf.write_text(json.dumps(s_tier, ensure_ascii=False))

    header = f'📈 股票 S级候选 @ {ts}'
    cond = "条件: 大股东控盘≥40% 且 score≥30; A股=akshare十大流通股东(剔除HKSCC/基金), 港股美股=yfinance(内部人+机构); S级触发启动/回撤/止盈/技术面"
    body_lines = [header, cond, '']
    MKTS = ['A股', '港股', '美股', '韩股']
    for mk in MKTS:
        group = [c for c in cands if c['_mkt'] == mk]
        if not group:
            continue
        group.sort(key=lambda c: (-(c['_conc'] if c['_conc'] is not None else -1), -(c['_score'] or 0)))
        s_count = sum(1 for c in group if c['_tier'] == 'S')
        body_lines.append(f"=== {mk} ({len(group)}只候选, {s_count}个S级) ===")
        for c in group:
            conc = c['_conc']
            conc_s = f"{conc:.1f}%" if conc is not None else "?"
            body_lines.append(f"  [{c['_tier']}] {c.get('name','?')} ({c.get('symbol','?')}) 大股东{conc_s} score {c['_score']:.0f}")
        body_lines.append('')
    full = '\n'.join(body_lines)
    print(full)

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    changed = bool(new or gone) and bool(prev)
    if changed:
        def _by_mkt(items):
            """[(名称, 市场)] → 按市场分段的文本。原实现是一行逗号拼接, 120 个名字混在一起。"""
            buckets = {}
            for name, mkt in items:
                buckets.setdefault(mkt, []).append(name)
            return '\n'.join(f"  {mk}({len(buckets[mk])}): " + ', '.join(buckets[mk])
                             for mk in MARKET_ORDER if buckets.get(mk))

        diff = []
        if new:
            diff.append('🆕 新增S级\n' + _by_mkt([(cur[x]['name'], market_of(cur[x].get('symbol', '')))
                                                 for x in sorted(new)]))
        if gone:
            diff.append('🚪 退出\n' + _by_mkt([(g, market_of(
                next((c.get('symbol', '') for c in sa if c.get('name') == g), ''))) for g in gone]))
        body = header + '\n\n' + '\n\n'.join(diff)
        if sa:
            body += '\n\n📌 当前S级 (' + str(len(sa)) + '只)\n' + _by_mkt(
                [(c.get('name', ''), market_of(c.get('symbol', ''))) for c in sa])
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
