"""Daily sample-market context plus registration help. Uses existing read-only snapshots."""
import json
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from holdings_core import number


def registration_hint(domain, bot=None):
    lines = {
        'alpha': '/buy 完整BSC合约地址 实际均价（USDT；不填数量）',
        'meme': '/buy 链名 完整合约地址 实际均价 [数量]（USD）\n链名 bsc/solana/robinhood/arc/stable/xlayer',
        'stock': '/buy a 600519.SS 实际均价 [股数]（CNY）\n/buy hk 0700.HK 实际均价 [股数]（HKD）\n/buy us NVDA 实际均价 [股数]（USD）',
    }
    target = 'https://t.me/' + bot if bot else '原来接收本雷达消息的 Telegram 机器人'
    return ('📝 实际持仓登记提示\n打开 ' + target + ' 的私聊，先发 /start，再按格式填写：\n'
            + lines[domain] + '\n只登记已经成交的实际整仓均价；代码仅示范格式。钉钉不接收登记命令。\n'
            '/positions 查看编号；/trend 查看分析；/help 查看完整说明。登记、结束跟踪均不执行交易。')


def slots(now):
    bj = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai'))
    ny = datetime.fromtimestamp(now, ZoneInfo('America/New_York'))
    due = []
    if bj.hour == 9 and bj.minute < 30:
        due.extend((domain, domain, bj.date().isoformat()) for domain in ('alpha', 'meme'))
    if bj.weekday() < 5 and bj.hour == 10 and bj.minute < 30:
        due.extend(('stock', market, bj.date().isoformat()) for market in ('A股', '港股'))
    if ny.weekday() < 5 and ny.hour == 10 and ny.minute < 30:
        due.append(('stock', '美股', ny.date().isoformat()))
    return due


def snapshot(domain):
    base = Path('/var/lib/' + domain + '-radar')
    if domain == 'alpha':
        return json.loads((base / 'latest.json').read_text())
    path = base / (domain + '-radar.sqlite')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        row = db.execute("SELECT value FROM runtime WHERE key='latest'").fetchone()
        return json.loads(row[0]) if row else {}
    finally:
        db.close()


def timestamp(value):
    n = number(value)
    if n is not None:
        return n
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def render(domain, market, report, now, bot=None):
    label = {'alpha': '币安Alpha', 'meme': 'Meme', 'stock': market}[domain]
    current = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai'))
    lines = [f'📌 {label}每日行情研判 · 北京时间{current:%Y-%m-%d %H:%M}',
             '范围仅为本雷达当前样本，不代表全市场，也不是买入推荐。']
    stamp = timestamp(report.get('as_of'))
    available = stamp is not None and 0 <= now - stamp <= (5400 if domain == 'alpha' else 1200)
    if not available:
        lines.append('快照缺失或过期，暂停方向研判；本条仅说明数据状态。')
    elif domain == 'stock':
        rows = (report.get('markets') or {}).get(market, [])
        fresh = [r for r in rows if r.get('fresh') and number(r.get('quote_at')) is not None and 0 <= now - r['quote_at'] <= 900]
        coverage = (report.get('data_quality') or {}).get('by_market', {}).get(market, {})
        lines.append(f'股票池 {coverage.get("configured", len(rows))}；获取 {coverage.get("fetched", len(rows))}；当前新鲜 {len(fresh)}；接口错误 {coverage.get("source_errors", "未知")}。')
        lines.extend(movement(fresh, 'change', '相对上一交易日末根有效K线'))
        if not fresh:
            lines.append('本市场休市、停牌或报价过期/缺失；不据此判断偏强偏弱，不将其他市场数据替代本市场。')
    elif domain == 'alpha':
        rows = report.get('candidates') or []
        fresh = [r for r in rows if number(r.get('ticker_time')) is not None and 0 <= now - float(r['ticker_time']) / 1000 <= 180]
        lines.append(f'Alpha与合约筛选样本 {len(rows)}；当前新鲜报价 {len(fresh)}；本轮数据错误 {len(report.get("errors") or [])}。')
        lines.extend(movement(fresh, 'change24h', '24小时变化'))
        lines.append('持仓集中、关联钱包、解锁与可卖性仍需逐币复核，筛选分不是上涨概率。')
        hypothesis = report.get('alpha_hypothesis') or {}
        checked = timestamp(hypothesis.get('as_of'))
        lines.append('帖子规则独立观察组（不加买入分）：Alpha＋合约、未发现现货同名、原始Top10≥90%、OI名义金额≥500万美元，并过滤已大涨。')
        if checked is not None and 0 <= now - checked <= 5400:
            items = hypothesis.get('rows') or []
            matched = [r for r in items if r.get('matched')]
            lines.append(f'最近一批轮查{len(items)}个；通过数据条件{len(matched)}；待核验{sum(bool(r.get("unknown")) for r in items)}。这不是全池完整名单。')
            for item in matched[:3]:
                lines.append(f'{item["symbol"]}：原始Top10 {item["raw_top10_share"]:.1%}；OI {item["open_interest_usd"]/1e6:.2f}百万美元；地址 {item["address"]}')
        else:
            lines.append('帖子规则本轮结果缺失/过期，不生成名单。')
        lines.append('原始集中度可能含池/交易所/桥；尚未证明控盘。OI同时含多空，不是庄家买入或百倍概率证据。')
    else:
        rows = report.get('rows') or []
        fetched = [r for r in rows if number(r.get('fetched_at')) is not None and 0 <= now - r['fetched_at'] <= 600]
        lines.append(f'发现交易池 {report.get("pairs_seen", 0)}；观察候选 {len(rows)}；近10分钟取得快照 {len(fetched)}。取得时间不等于最后成交时间。')
        safety = report.get('safety') or {}
        lines.append(f'第三方安全统计：未列出风险 {safety.get("safe", 0)} / 有风险 {safety.get("blocked", 0)} / 未知 {safety.get("unknown", 0)}。未列出风险不等于保证安全。')
        lines.extend(movement(fetched, 'change_1h', '供应商报告的1小时变化，仅作快照背景'))
        lines.append('链覆盖：' + '；'.join(f'{k} 候选{v.get("candidates", 0)} / {v.get("status", "未知")}' for k, v in (report.get('chains') or {}).items()))
        lines.append('可卖性未验证、关键安全字段缺失的币只观察；不能据此建议买入。')
    if available:
        lines.append('快照生成：' + datetime.fromtimestamp(stamp, ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M:%S 北京时间'))
    lines.append(registration_hint(domain, bot))
    return '\n'.join(lines)


def movement(rows, field, basis):
    values = [number(row.get(field)) for row in rows]
    values = [v for v in values if v is not None]
    if not values:
        return ['缺少可用涨跌样本，方向未知。']
    median = statistics.median(values)
    direction = '样本多数上涨' if sum(v > 0 for v in values) / len(values) >= .6 else '样本多数下跌' if sum(v < 0 for v in values) / len(values) >= .6 else '样本分化'
    return [f'{basis}：有效样本{len(values)}，上涨{sum(v > 0 for v in values)} / 下跌{sum(v < 0 for v in values)} / 持平{sum(v == 0 for v in values)}；中位数{median:+.2f}%。',
            f'样本观察：{direction}；候选池存在筛选偏差，不推断后续收益。']


def queue_daily(db, configured, now, enqueue, read=snapshot):
    result = []
    for domain, market, day in slots(now):
        key = f'daily-brief-v1:{domain}:{market}:{day}'
        if db.execute('SELECT 1 FROM runtime WHERE key=?', (key,)).fetchone():
            continue
        try:
            report = read(domain)
        except (OSError, ValueError, sqlite3.Error):
            report = {}
        if domain == 'alpha':
            hypothesis = db.execute("SELECT value FROM runtime WHERE key='alpha_hypothesis'").fetchone()
            if hypothesis:report = dict(report, alpha_hypothesis=json.loads(hypothesis[0]))
        bot_row = db.execute('SELECT value FROM runtime WHERE key=?', ('bot:' + domain,)).fetchone()
        bot = json.loads(bot_row[0]) if bot_row else None
        text = render(domain, market, report, now, bot)
        with db:
            enqueue(db, key, domain, configured[domain], text, now)
            db.execute('INSERT INTO runtime VALUES(?,?)', (key, json.dumps({'created': now})))
        result.append({'domain': domain, 'market': market, 'day': day})
    return result
