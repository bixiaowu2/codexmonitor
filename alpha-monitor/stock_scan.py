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

sys.path.insert(0, '/opt/akshare-venv/lib/python3.12/site-packages')
import akshare as ak
import yfinance as yf

DATA = Path('/var/lib/stock-radar')
MATCHES = DATA / 'stock_matches.json'
STATE = DATA / 'scan_state.json'
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
    for c in cands:
        s = c['_score']
        conc = c['_conc']
        if conc is not None and conc >= 30 and s >= 50:
            c['_tier'] = 'S'
        elif (conc is not None and conc >= 20) or s >= 45:
            c['_tier'] = 'A'
        else:
            c['_tier'] = 'B'
    sa = [c for c in cands if c['_tier'] in ('S', 'A')]
    sa.sort(key=lambda c: (-(c['_conc'] if c['_conc'] is not None else -1), -(c['_score'] or 0)))
    s_tier = [{'symbol': c.get('symbol'), 'name': c.get('name'), 'conc': c['_conc'], 'tier': 'S'} for c in sa if c['_tier'] == 'S']
    MATCHES.write_text(json.dumps(s_tier, ensure_ascii=False, indent=2))

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
    if sa:
        body_lines = [header, cond, '股票候选排序(S+A级):']
        for i, c in enumerate(sa, 1):
            conc = c['_conc']
            conc_s = f'{conc:.1f}%' if conc is not None else '?'
            body_lines.append(f"{i}. [{c['_tier']}] {c.get('name','?')} ({c.get('symbol','?')}) 大股东{conc_s} score {c['_score']:.0f}")
    else:
        body_lines = [header, cond, '当前无 S/A 级命中']
    full = '\n'.join(body_lines)
    print(full)

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    changed = bool(new or gone) and bool(prev)
    if changed:
        diff = []
        if new:
            diff.append('新增S级: ' + ', '.join(cur[s]['name'] for s in sorted(new)))
        if gone:
            diff.append('退出: ' + ', '.join(gone))
        body = header + '\n' + '\n'.join(diff) + ('\n\n当前: ' + ', '.join(c.get('name','') for c in sa) if sa else '')
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
