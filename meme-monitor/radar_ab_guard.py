#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""radar-ab-guard —— A/B/C 实验的饥饿与断流守卫（只读，每 2 小时一次）

为什么需要（2026-11-14 夜加）：
  · 新候选门槛曾把开仓压到 0.97 笔/天而三天没人发现（radar-daily-check 一天只跑一次，
    最坏要 24 小时才发现实验静默停摆）。
  · 现在 A/B 要连续跑约 7 天凑 150 笔，停摆一天的代价是 1/7 的实验周期。
  · heartbeat-check 只覆盖「服务挂了」，覆盖不了「服务正常但一个候选都不出」。

两条判据：
  1. signals 断流：max(created) 距今 > 45 分钟 → 采集链路断了。
  2. 候选饥饿：A/B/C 三臂最近一次开仓距今 > 6 小时（部署满 6 小时后才判）→ 门槛/链路异常。
     Poisson 参考：21 笔/天时 6 小时零开仓的概率约 0.5%，误报可接受。

告警走信箱（home → lead, L2），同类告警 6 小时内不重复。状态存 /var/lib/meme-radar/ab-guard.state。
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

RADAR_DB = '/var/lib/meme-radar/meme-radar.sqlite'
AB_DB = '/var/lib/meme-radar/paper_trade_ab.sqlite'
STATE = '/var/lib/meme-radar/ab-guard.state'
DEPLOY = 1794666600.0          # 2026-11-14 14:30Z 新候选门槛上线
MARKS = ('ab_h2_v1', 'ab_h2_v1_early30', 'ab_h2_v1_time30')
SIGNAL_STALE_SEC = 45 * 60
OPEN_STARVE_SEC = 6 * 3600
REALERT_SEC = 6 * 3600


def q(db, sql, args=()):
    c = sqlite3.connect('file:%s?mode=ro' % db, uri=True, timeout=15)
    try:
        return c.execute(sql, args).fetchone()
    finally:
        c.close()


def load_state():
    try:
        with open(STATE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def send_mail(subject, body):
    text = subject + '\n\n' + body + '\n\n（radar-ab-guard 自动告警，只读巡检，未改任何规则）\n'
    try:
        p = subprocess.run(['/opt/muse/mail_send.sh', 'home', 'lead', 'L0'],
                           input=text, text=True, capture_output=True, timeout=60)
        return p.returncode == 0
    except Exception as e:
        print('发信失败: %s: %s' % (type(e).__name__, e))
        return False


def main():
    now = time.time()
    alerts = []

    try:
        last_sig = q(RADAR_DB, 'SELECT max(created) FROM signals')[0] or 0
    except Exception as e:
        last_sig = 0
        print('读 signals 失败: %s' % e)
    sig_age = (now - last_sig) / 60 if last_sig else 1e9
    if sig_age > SIGNAL_STALE_SEC / 60:
        alerts.append(('signals', 'signals 断流：最新一条距今 %.0f 分钟（阈值 45）' % sig_age))

    last_open = 0
    n24 = -1
    try:
        for m in MARKS:
            v = q(AB_DB, 'SELECT max(opened) FROM positions WHERE mark=?', (m,))[0] or 0
            last_open = max(last_open, v)
        n24 = q(AB_DB, 'SELECT count(*) FROM positions WHERE opened>=?', (now - 86400,))[0]
    except Exception as e:
        print('读 A/B 台账失败: %s' % e)
    anchor = max(last_open, DEPLOY)
    open_age_h = (now - anchor) / 3600
    if now > DEPLOY + OPEN_STARVE_SEC and open_age_h > OPEN_STARVE_SEC / 3600:
        alerts.append(('starve',
                       'A/B/C 候选饥饿：最近一次开仓 %s，距今 %.1f 小时（阈值 6）；'
                       '近 24h 新开仓 %d 笔' % (
                           time.strftime('%m-%d %H:%MZ', time.gmtime(last_open)) if last_open else '无',
                           open_age_h, n24)))

    st = load_state()
    sent = 0
    for kind, msg in alerts:
        prev = float(st.get(kind) or 0)
        print('ALERT[%s] %s' % (kind, msg))
        if now - prev >= REALERT_SEC:
            ok = send_mail('## [home] 自动告警：%s' % msg.split('：')[0], msg)
            if ok:
                st[kind] = now
                sent += 1
    if not alerts:
        print('ok: signals %.1f 分钟前 | A/B/C 最近开仓 %.1f 小时前 | 近 24h 开仓 %d 笔'
              % (sig_age, open_age_h, n24))
    save_state(st)
    return 0


if __name__ == '__main__':
    sys.exit(main())
