"""纸面交易满 N 笔时自动出报告并推 Telegram（只报一次）。

为什么单独一个脚本而不是改 paper_trade.py:
  paper_trade.py 每 10 分钟跑一次, 是"推进交易"; 这个是"到点汇报", 频率完全不同(每天一次)。
  分开跑, 互不干扰。

触发条件: 已平仓 >= TARGET(默认 20) 且尚未汇报过。
汇报后写 `/var/lib/meme-radar/paper_reported.json` 防止重复推送。
"""
from __future__ import annotations

import json, os, sqlite3, time, urllib.request
from pathlib import Path

DATA = Path('/var/lib/meme-radar')
DB = DATA / 'paper_trade.sqlite'
MARK = DATA / 'paper_reported.json'
TARGET = int(os.environ.get('PAPER_TARGET', '20'))


def tg_send(text):
    token = os.environ.get('MEME_TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.environ.get('MEME_TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        print('缺 Telegram 凭据')
        return False
    body = json.dumps({'chat_id': chat, 'text': text,
                       'disable_web_page_preview': True}).encode()
    req = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage',
                                 data=body, headers={'Content-Type': 'application/json'})
    try:
        return bool(json.loads(urllib.request.urlopen(req, timeout=15).read()).get('ok'))
    except Exception as e:
        print('TG 失败:', str(e)[:100])
        return False


def build_report():
    db = sqlite3.connect(str(DB), timeout=15)
    db.row_factory = sqlite3.Row
    rows = db.execute('SELECT * FROM positions WHERE closed=1').fetchall()
    opens = db.execute('SELECT COUNT(*) FROM positions WHERE closed=0').fetchone()[0]
    # 注意: 下面还要用 db 查平仓时间, 先别 close

    if len(rows) < TARGET:
        return None
    # 按**平仓时间**排序(取该笔最后一笔 fill 的 at), 才是真实的收益曲线
    timed = []
    for r in rows:
        row = db.execute('SELECT MAX(at) FROM fills WHERE key=?', (r['key'],)).fetchone()
        timed.append((row[0] or r['opened'], r['realized']))
    timed.sort()
    rets = sorted(x for _, x in timed)
    n = len(rets)
    wins = sum(1 for x in rets if x > 0)
    avg = sum(rets) / n
    # 累计收益(等权, 每笔 1 单位)
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    db.close()
    for _, x in timed:
        eq += x
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    lines = [
        f'📊 纸面交易报告（已平仓 {n} 笔）',
        '',
        f'胜率      {wins * 100 // n}%',
        f'中位收益  {rets[n // 2] * 100:+.1f}%',
        f'平均收益  {avg * 100:+.1f}%',
        f'最高/最低 {rets[-1] * 100:+.0f}% / {rets[0] * 100:+.0f}%',
        f'累计      {eq * 100:+.0f}%（等权，每笔 1 单位）',
        f'最大回撤  {mdd * 100:.0f}%',
        '',
        ('✅ 期望值为正 —— 可以讨论接真钱（先小仓）' if avg > 0
         else '❌ 期望值不为正 —— 不建议接真钱'),
        '',
        f'规则: 止损-50% · TP1 2x · TP2 5x · 剩余回撤30%',
        f'未平仓 {opens} 笔',
    ]
    return '\n'.join(lines)


def main():
    if MARK.exists():
        print('已汇报过，跳过')
        return
    if not DB.exists():
        print('还没有纸面交易库')
        return
    text = build_report()
    if not text:
        print(f'已平仓不足 {TARGET} 笔')
        return
    print(text)
    if tg_send(text):
        MARK.write_text(json.dumps({'at': time.time(), 'text': text}, ensure_ascii=False))
        print('已推送 Telegram')
    else:
        print('推送失败，下次重试')


if __name__ == '__main__':
    main()
