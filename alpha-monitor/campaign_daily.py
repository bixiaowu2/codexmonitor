#!/usr/bin/env python3
"""币安神算子活动帖 · 每日自动发布。

【为什么需要它】
  广场发文 +100/天，是活动里最稳的攒分方式（交易要花 $50/天，预测要盈利）。
  但要连发到 2026-11-16 定榜 —— 26 天，手动记不住。

【防重（最重要）】
  活动贴每天限 1 条，多发不加分，而且会撞「禁批量雷同」的反滥用红线。
  发帖有两条路（服务器脚本 + 总指挥的保管箱通道）共用一个账号 ——
  所以发前**必须**先读台账 chat/square-log.md，当天已有活动贴就退出。

【流程】
  1. 读台账，看今天（东八区）有没有活动贴
  2. 没有 → 生成 → 发布 → 追加台账 → 推 Telegram 通知
  3. 有 → 静默退出

用法：
    python3 campaign_daily.py             # 正常跑（带防重）
    python3 campaign_daily.py --dry-run   # 只看不发
    python3 campaign_daily.py --force     # 跳过防重（手动补发用，谨慎）
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, '/opt/alpha-radar')
import square_campaign as sc

LEDGER = Path('/var/lib/content-queue/video-requests/chat/square-log.md')
CST = datetime.timezone(datetime.timedelta(hours=8))


def today_cst():
    return datetime.datetime.now(CST).strftime('%Y-%m-%d')


def has_posted_today():
    """台账里今天有没有活动贴。

    返回 (有, 说明)。台账行格式：
      - 2026-10-21 22:36 | 通道 | 短贴 | id=... | 链接 | 活动贴：是
    """
    if not LEDGER.exists():
        return False, '台账不存在（视为今天没发）'
    today = today_cst()
    try:
        txt = LEDGER.read_text(encoding='utf-8')
    except Exception as e:
        return False, f'台账读不了（{type(e).__name__}）—— 保守起见视为已发'
    for line in txt.splitlines():
        if today not in line:
            continue
        if '活动贴：是' in line or '活动贴:是' in line:
            return True, line.strip()[:90]
    return False, f'台账里 {today} 没有活动贴记录'


def append_ledger(vid, vid_out, text):
    """发成功后立即记账 —— 两路共用一个账号，不记就会撞。"""
    now = datetime.datetime.now(CST).strftime('%Y-%m-%d %H:%M')
    m = re.search(r'(?:post[_ ]?id|id)[=:]\s*([0-9]+)', vid_out or '', re.I)
    pid = m.group(1) if m else '?'
    line = (f'- {now} | 服务器脚本(square_campaign) | 短贴 | id={pid} | '
            f'变体V{vid} | 活动贴：是\n')
    try:
        with LEDGER.open('a', encoding='utf-8') as f:
            f.write(line)
        print(f'  已记账: {line.strip()}')
    except Exception as e:
        print(f'  ⚠️ 记账失败（{type(e).__name__}）—— 手动补一条，否则明天可能重发')


def notify(text, posted):
    """推 Telegram 通知（复用 content-push 的 relay）。"""
    try:
        sys.path.insert(0, '/opt/content-push')
        import relay
        head = '📮 [神算子] 活动帖已自动发布' if posted else '⚠️ [神算子] 活动帖发布失败'
        relay.send(f'{head}\n\n{text[:800]}')
    except Exception as e:
        print(f'  通知失败: {type(e).__name__}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true', help='跳过防重（谨慎）')
    a = ap.parse_args()

    today = today_cst()
    posted, why = has_posted_today()
    print(f'今天（东八区 {today}）：{"已有活动贴" if posted else "还没有"} —— {why}')

    if posted and not a.force:
        print('防重命中，退出（活动贴每天限 1 条，多发不加分）')
        return 0

    d = sc.fetch()
    vid = sc.pick_variant(d)
    text = sc.compose(d, vid)
    print(f'\n=== 变体 V{vid} · {d["markets"]} 市场 / {d["n"]} 个机会 ===')
    print(text)
    print('-' * 56)

    warns = sc.ps.audit(text, platform='square')
    for w in warns:
        print(f'[风格告警] {w}')

    # 活动硬条件：不满足就不发（发了也不计分）
    n_chars = len(text.replace('\n', ''))
    if sc.TAG not in text:
        print(f'❌ 缺 {sc.TAG}，发了也不计分 —— 中止')
        notify(text, False)
        return 1
    if n_chars <= 20:
        print(f'❌ 正文 {n_chars} 字符（要求 >20）—— 中止')
        notify(text, False)
        return 1
    if warns:
        print('⚠️ 有风格告警 —— 仍会发（告警不阻断），但记下来')

    if a.dry_run:
        print('\n[dry-run] 未发布')
        return 0

    code, out = sc.post(text)
    if code == 0:
        append_ledger(vid, out, text)
        notify(text, True)
        print('✅ 已发布')
    else:
        notify(text, False)
        print('❌ 发布失败（台账未记，明天会自动重试）')
    return code


if __name__ == '__main__':
    sys.exit(main())
