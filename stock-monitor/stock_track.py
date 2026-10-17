#!/usr/bin/env python3
"""股票候选跟踪器(只读)。对 stock-radar 的高分候选分级跟踪。

分级(按 score_meta.score):
  S级: score ≥ 60 且 fresh == True  → 触发启动/回撤/止盈信号
  A级: score ≥ 40                   → 只排序不推信号
  B级: 其余                          → 仅记录

信号(股票无合约费率/OI, 用价格):
  B. 启动: 单日涨幅 ≥ +5% → 推;
  A. 回撤: 从近期高点回落 ≥ 10% → 推;
  T. 分批止盈: 较首次发现价 +50% / +100% → 推。
所有推送 TG + 钉钉。休市(fresh=False)时价格不更新, 信号自然不触发。
"""
from __future__ import annotations
import sys, json, time, types, os
from pathlib import Path
sys.path.insert(0, '/opt/stock-radar')
from notify import send_channel
from config import Config
from markets import market_of, MARKET_ORDER

DATA = Path('/var/lib/stock-radar')
STATE = DATA / 'track_state.json'


# ---------- 钉钉降频 ----------
# 钉钉自定义机器人有企业级月配额(实测 errcode 90030 会耗尽), 而这几套雷达每轮都直发。
# 故把钉钉改为"入缓冲 + 每 DT_INTERVAL 汇总一条"; Telegram 不受影响, 仍即时。
DT_INTERVAL = 3600
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
DRAWDOWN_PCT = 0.10
PUMP_1D = 0.05
TP1, TP2 = 0.50, 1.00


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
        dingtalk_keyword=env.get('STOCK_DINGTALK_KEYWORD', 'Stock雷达').strip(),
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
def get_matches():
    try:
        f = DATA / 'stock_matches.json'
        if not f.exists():
            return []
        return json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return []


def check_change_unit(latest):
    """兜底: runtime.latest 的 change 约定是**百分比**(-7.19 表示 -7.19%)。

    这个单位是跨文件的隐式契约(stock-radar.sqlite ← stock_track.py), 上游哪天改成小数,
    这里会静默错 100 倍(跌幅永不触发 / 涨幅变成 8% 的误报)。故用中位数探一下:
    正常交易日 A 股/港股的 |change| 中位数约 0.5~3; 若掉到 0.2 以下, 基本可判定单位变了。
    """
    vals = sorted(abs(r['change']) for r in latest.values()
                  if isinstance(r.get('change'), (int, float)) and r['change'])
    if len(vals) >= 5:
        med = vals[len(vals) // 2]
        if med < 0.2:
            print(f'⚠️ change 单位可疑: 中位 |change|={med:.4f}, 像小数而非百分比 —— '
                  f'请核对 stock_track 的 /100 换算是否仍成立')


def load_latest():
    """从 stock-radar 的 runtime.latest 读全量行情。

    stock_matches.json 只存了 tier/conc(scan 写 S 级时没带价格),
    价格与涨跌幅在 latest 里, 信号分支必须从这里取, 否则恒为 None。
    """
    out = {}
    try:
        import sqlite3 as _sq
        c = _sq.connect(str(DATA / 'stock-radar.sqlite'))
        r = c.execute("SELECT value FROM runtime WHERE key='latest'").fetchone()
        if r:
            for row in json.loads(r[0]).get('rows', []):
                out[row.get('symbol')] = row
    except Exception:
        pass
    return out


def main():
    env = dict(os.environ)
    matches = get_matches()
    if not matches:
        print('无股票候选, 跳过')
        return
    latest = load_latest()
    check_change_unit(latest)
    # 信任 stock_scan 的分级(它已写入 tier/conc 字段); 价格/涨跌从 latest 补。
    # 注意: latest 里的 change 是百分比(如 -1.06), 本文件按小数比较, 故 /100。
    for m in matches:
        m['_score'] = 0
        m['_tier'] = m.get('tier', 'B')
        row = latest.get(m.get('symbol')) or {}
        m['price'] = row.get('price')
        ch = row.get('change')   # 单位: 百分比(见 check_change_unit 说明)
        m['change'] = (ch / 100.0) if ch is not None else None
        m['fresh'] = bool(row.get('fresh'))
    s_tier = [m for m in matches if m.get('tier') == 'S']
    sa = [m for m in matches if m.get('tier') in ('S', 'A')]

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    drawn = set(state.get('drawn', []))
    pump_alerted = set(state.get('pump_alerted', []))
    tp_hit = set(state.get('tp_hit', []))
    first_seen = state.get('first_seen', {})

    alerts = {}          # 按市场分桶: {市场: [告警行]}
    ts = time.strftime('%m-%d %H:%M UTC', time.gmtime())
    for m in s_tier:
        sym = m.get('symbol', '?')
        name = m.get('name', '')
        price = m.get('price')
        chg = m.get('change')  # 较上日百分比(小数)
        if not m.get('fresh') or not price:
            continue  # 休市/无行情: 不产生信号, 免得拿昨收当今日异动
        if sym not in first_seen and price:
            first_seen[sym] = price
        base = first_seen.get(sym)
        # T 分批止盈
        if base and price and base > 0:
            mult = price / base - 1
            if mult >= TP2 and sym + ':tp2' not in tp_hit:
                alerts.setdefault(market_of(sym), []).append(f"💰 第二档止盈 · {name} ({sym})\n较发现价 +{mult*100:.0f}% (现 {price:.2f})。建议再出 1/3。")
                tp_hit.add(sym + ':tp2')
            elif mult >= TP1 and sym + ':tp1' not in tp_hit:
                alerts.setdefault(market_of(sym), []).append(f"💰 第一档止盈 · {name} ({sym})\n较发现价 +{mult*100:.0f}% (现 {price:.2f})。建议出 1/3 锁利润。")
                tp_hit.add(sym + ':tp1')
        # B 启动(单日大涨)
        if chg is not None and chg >= PUMP_1D and sym not in pump_alerted:
            alerts.setdefault(market_of(sym), []).append(f"🚀 启动信号 · {name} ({sym})\n单日大涨 {chg*100:+.1f}%, 可能启动。")
            pump_alerted.add(sym)
        # A 回撤(change 为负且幅度大)
        if chg is not None and chg <= -0.07 and sym not in drawn:
            alerts.setdefault(market_of(sym), []).append(f"⚠️ 回撤预警 · {name} ({sym})\n单日急跌 {chg*100:.1f}%, 注意止损。")
            drawn.add(sym)
        elif chg is not None and chg > -0.02:
            drawn.discard(sym)

    state.update({'drawn': list(drawn), 'pump_alerted': list(pump_alerted), 'tp_hit': list(tp_hit), 'first_seen': first_seen, 'last_run': time.time()})
    STATE.write_text(json.dumps(state, ensure_ascii=False))

    if alerts:
        # header 里的"当前S级"也按市场分组, 否则一样是混的
        tier_bits = []
        for _mk in MARKET_ORDER:
            _names = [m.get('name', m.get('symbol', '?')) for m in s_tier
                      if market_of(m.get('symbol', '')) == _mk]
            if _names:
                tier_bits.append(f"{_mk}({len(_names)}): " + ', '.join(_names))
        header = (f"🎯 股票S级跟踪 @ {ts}\n"
                  + '\n'.join(tier_bits) + '\n')
        blocks = []
        for _mk in MARKET_ORDER:
            _a = alerts.get(_mk)
            if _a:
                blocks.append(f"━━━ {_mk} ({len(_a)}条) ━━━\n" + '\n\n'.join(_a))
        body = header + '\n\n'.join(blocks)
        print(body)
        notify(env, body)
    else:
        print(f'监控 {len(s_tier)} 个股票S级候选, 无异动。')

    if time.time() - state.get('last_summary', 0) >= 24 * 3600 and sa:
        latest_rows = latest
        lines = [f'📊 股票候选日更 @ {ts}(S+A级, 大股东控盘筛选)']

        def _mkt(sym):
            sym = sym or ''
            if sym.endswith('.SS') or sym.endswith('.SZ'):
                return 'A股'
            if sym.endswith('.HK'):
                return '港股'
            if '.KS' in sym:
                return '韩股'
            return '美股'

        any_fresh = False
        for mk in ('A股', '港股', '美股', '韩股'):
            group = [m for m in sa if _mkt(m.get('symbol')) == mk]
            if not group:
                continue
            n_s = sum(1 for m in group if m.get('tier') == 'S')
            lines.append(f"── {mk} ({len(group)}只, {n_s}个S级) ──")
            for m in group:
                row = latest_rows.get(m.get('symbol'), {})
                score = row.get('score_meta', {}).get('score', 0) if isinstance(row.get('score_meta'), dict) else 0
                px = row.get('price')
                cur = row.get('currency') or ''
                fresh = bool(row.get('fresh'))
                any_fresh = any_fresh or fresh
                px_s = f"{px:.2f}{cur}" if px else '?'
                conc = m.get('conc')
                conc_s = f" 大股东{conc:.0f}%" if conc else ''
                tag = '' if fresh else '(昨收)'
                lines.append(f"  [{m.get('tier','?')}] {m.get('name','?')} 大股东{conc_s} score {score:.0f} 价 {px_s}{tag}")
        # 全部休市且无变化 → 没信息量, 不推(省额度); 有 fresh 或刚进 S 级才推
        if any_fresh or state.get('last_summary', 0) == 0:
            notify(env, '\n'.join(lines))
        else:
            print('全市场休市, 日更跳过(无信息量)')
        state['last_summary'] = time.time()
        STATE.write_text(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
