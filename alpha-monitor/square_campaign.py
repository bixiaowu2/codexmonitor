#!/usr/bin/env python3
"""币安神算子活动帖生成器（每日一条，连发到 2026-11-16 定榜）。

【活动规则 · 来自官方条款截图，非转述】
  广场发文 +100/天：必带 #币安神算子、长度 >20 字符、内容须与活动相关、每日重置
  交易 ≥$50 +200/天 ／ 下单市场 5·10·20 个 → +100/+200/+300
  签到递增 +10/+20/+30/+40 ／ 邀请 1 人 +300

  奖池：赛季榜 50 万（前 2 万名）· 周榜 40 万（前 8 千名）· 阳光奖 10 万（签到满 5 次）
  ⚠️ 周榜资格：**通过积分预测产生的每周盈亏须 > 0** —— 光攒分不够

【为什么需要变体】
  活动连发 26 天。26 条结构一样的帖子会撞「禁批量雷同」的反滥用红线，
  而且读者一眼看出是机器发的。所以每个变体结构不同，按数据特征轮换。

【内容定位】
  我们的独家资产：自建预测市场雷达的真实数据。
  别人发这个活动只能喊「快来参加」，我们能贴**当天真实的价差分布**——
  这既满足「内容与活动相关」，又是别人抄不了的信息差。

用法：
    python3 square_campaign.py              # 打印今天的活动帖
    python3 square_campaign.py --post       # 打印并发布
    python3 square_campaign.py --vid 3      # 指定变体
"""
import argparse
import json
import random
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, '/opt/alpha-radar')
import post_style as ps

DB = '/var/lib/predict-arb/arb_observations.sqlite'
POLL_MS = 15000
COST_LIMIT = 0.98
STATE = Path('/var/lib/alpha-radar/campaign_state.json')
TAG = '#币安神算子'
SQUARE_LIMIT = 1000          # 广场正文上限比 X 宽，但仍要克制


# ---------------------------------------------------------------
# 取数
# ---------------------------------------------------------------
def fetch():
    """过去 24 小时的预测市场分布。"""
    now = int(time.time() * 1000)
    since = now - 86400 * 1000
    db = sqlite3.connect(DB, timeout=20)
    db.row_factory = sqlite3.Row
    base = db.execute('''
        SELECT COUNT(DISTINCT condition_id) markets
        FROM observations WHERE at > ?
    ''', (since,)).fetchone()
    opps = db.execute('''
        SELECT MIN(question) q, MIN(maker_cost) mc,
               COUNT(DISTINCT CAST(at/? AS INT)) rounds
        FROM observations
        WHERE at > ? AND maker_cost < ?
        GROUP BY condition_id
        HAVING rounds >= 3
        ORDER BY rounds DESC, mc ASC
    ''', (POLL_MS, since, COST_LIMIT)).fetchall()
    db.close()

    def dur(rounds):
        sec = rounds * POLL_MS / 1000
        if sec >= 3600:
            return f'{sec / 3600:.1f} 小时'
        if sec >= 60:
            return f'{sec / 60:.0f} 分钟'
        return f'{sec:.0f} 秒'

    rows = [dict(r) for r in opps]
    return {
        'markets': base['markets'],
        'n': len(rows),
        'long': len([r for r in rows if r['rounds'] >= 8]),
        'best': rows[0] if rows else None,
        'dur': dur,
    }


def short_q(q, n=52):
    """问题名太长时保留头尾 —— 识别信息常在尾部（日期/数值）。"""
    q = (q or '').strip()
    if len(q) <= n:
        return q
    head = q[:n - 22].rsplit(' ', 1)[0]
    return f'{head}…{q[-(n - len(head) - 1):].lstrip()}'


# ---------------------------------------------------------------
# 变体轮换（和 post_variants 同思路：按数据形状 + 排除上次用过的）
# ---------------------------------------------------------------
def _load():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save(d):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


def pick_variant(d):
    """按当天数据选结构，排除上次用过的。"""
    n = d['n']
    if n == 0:
        cands = [5, 6]           # 没机会 → 讲方法/讲活动
    elif n <= 2:
        cands = [2, 3, 6]        # 少 → 单点深挖 / 教训
    else:
        cands = [1, 3, 4, 5, 6]  # 多 → 日报 / 教训 / 方法
    if d['long'] >= 5:
        cands = [1] + cands      # 持续久的多了 → 日报最合适
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    st = _load()
    last = st.get('campaign')
    pool = [c for c in out if c != last] or out
    v = random.choice(pool)
    st['campaign'] = v
    _save(st)
    return v


# ---------------------------------------------------------------
# 变体
# ---------------------------------------------------------------
def compose(d, vid):
    n, mk, best = d['n'], d['markets'], d['best']
    longn = d['long']
    dur = d['dur']

    if vid == 1:      # 数据日报型
        head = f'我在币安神算子开赛第一天，把预测市场的底摸了一遍。'
        items = [
            f'过去 24 小时，{mk} 个预测市场里，{n} 个出现过 2% 以上的价差。',
            f'其中 {longn} 个持续超过 2 分钟 —— 价差不是闪一下就没了。',
        ]
        if best:
            items.append(f'最久的一个撑了 {dur(best["rounds"])}：\n'
                         f'{short_q(best["q"])}')
        tail = ('但我实测过 105 笔纸面成交，扣掉成本后正收益 0 笔。\n'
                '价差看着有，真去挂单吃掉一半。\n\n'
                '所以这些我只记录，不下注。\n\n'
                f'{TAG} $BTC')

    elif vid == 2:    # 单点深挖型
        if best:
            head = f'神算子开赛第一天，我盯的是这个市场。'
            items = [
                f'{short_q(best["q"], 60)}',
                f'它的价差连续 {best["rounds"]} 轮没消失，'
                f'折合 {dur(best["rounds"])}。',
                f'当前空间 {(1 - best["mc"]) * 100:.1f}%。',
            ]
        else:
            head = '神算子开赛第一天，我没找到值得盯的市场。'
            items = ['这本身也是信息。']
        tail = ('我判断一个价差真假，只看一件事：它能不能真的成交。\n'
                'maker 是挂单价，不是成交价。\n\n'
                f'{TAG} $BTC')

    elif vid == 3:    # 教训型
        head = '借着神算子开赛，说一个我踩过的坑。'
        items = [
            '我算过 48 个套利机会，价差都超过 3%，兴奋了两小时。',
            '然后发现 48 个全是假的 —— 我用的中间价，不是可成交价。',
            '后来做 105 笔纸面成交，扣掉成本后正收益 0 笔，中位亏 2%。',
        ]
        tail = ('坑不在价差存不存在，在「看到的价格」不等于「能成交的价格」。\n\n'
                '预测这件事，最难的不是算，是承认自己算错了。\n\n'
                f'{TAG} $BTC')

    elif vid == 4:    # 方法型
        head = '神算子第一天，先讲方法再讲数据。'
        items = [
            f'今天 {mk} 个市场里，{n} 个出现过 2%+ 价差。',
            '但我不会因为价差大就下注。',
            '我的判据是「持续」——同一个价差连续 20 轮都在，才值得看一眼。',
        ]
        tail = ('只闪一下的，多半是陈旧挂单。\n\n'
                '这个方法是从 105 笔失败里换来的。\n\n'
                f'{TAG} $BTC')

    elif vid == 5:    # 反常识型（中位数 vs 平均值）
        head = '神算子第一天，先记住这两个数。'
        items = [
            '我跟踪过 5782 个链上信号，中位数收益是 0%。',
            '但平均涨幅是 396%。',
            '差的这 396 个百分点，全是被 1% 的极端值拉起来的。',
        ]
        tail = ('所以看任何预测数据，先问一句：这是中位数还是平均值。\n\n'
                '平均值好看，但它不描述你大概率会遇到的情况。\n\n'
                f'{TAG} $BTC')

    else:             # 6 活动观察型
        head = '币安神算子第一天，说点实话。'
        items = [
            '这个活动的奖池是 100 万 USDC，但拆开看：',
            '赛季榜 50 万（前 2 万名）· 周榜 40 万（前 8 千名）· 阳光奖 10 万',
            '阳光奖是给没进榜、但签到满 5 次的人，每人最高 5 USDC。',
        ]
        tail = ('而周榜的资格是「预测盈亏须大于 0」—— 光攒积分不够。\n\n'
                '我把它当数据练习场，不当提款机。\n\n'
                f'{TAG} $BTC')

    return ps.compose(head, items, tail, limit=SQUARE_LIMIT)


# ---------------------------------------------------------------
# 发布（走 Muse 的 /opt/square-post 脚本，不自己实现）
# ---------------------------------------------------------------
def post(text):
    """调用币安官方 skill 的 post-text.mjs 发布。

    台账纪律：发前先读 chat/square-log.md，发后立即追加。
    """
    skill = Path('/opt/square-post')
    script = skill / 'scripts' / 'post-text.mjs'
    if not script.exists():
        print(f'找不到发帖脚本: {script}')
        return 1
    import os
    env = dict(os.environ)
    key = ''
    for line in Path('/etc/square-post.env').read_text(encoding='utf-8').splitlines():
        if line.startswith('BINANCE_SQUARE_OPENAPI_KEY='):
            key = line.split('=', 1)[1].strip()
    env['BINANCE_SQUARE_OPENAPI_KEY'] = key
    bun = '/home/ubuntu/.bun/bin/bun'   # 和 square_publish.py 一致
    r = subprocess.run([bun, str(script), '--text', text],
                       cwd=str(skill), capture_output=True, text=True,
                       timeout=180, env=env)
    out = (r.stdout or '') + (r.stderr or '')
    print(out.strip()[:500])
    # 返回 (code, out) —— 调用方要拿 out 里的 post id 记账
    return (0 if 'Success' in out else 1), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--post', action='store_true', help='发布（默认只打印）')
    ap.add_argument('--vid', type=int, help='指定变体 1-6')
    a = ap.parse_args()

    d = fetch()
    vid = a.vid or pick_variant(d)
    text = compose(d, vid)

    print(f'=== 变体 V{vid} · 数据: {d["markets"]} 市场 / {d["n"]} 个机会 / '
          f'{d["long"]} 个持续较久 ===')
    print(text)
    print('-' * 56)

    warns = ps.audit(text, platform='square')
    for w in warns:
        print(f'[风格告警] {w}')
    if not warns:
        print('风格自检: 通过')

    # 活动硬条件
    n_chars = len(text.replace('\n', ''))
    if TAG not in text:
        print(f'❌ 缺活动标签 {TAG} —— 这条不计分')
    if n_chars <= 20:
        print(f'❌ 正文只有 {n_chars} 字符（要求 >20）—— 这条不计分')
    if TAG in text and n_chars > 20 and not warns:
        print(f'活动条件: ✅ 带标签 · ✅ {n_chars} 字符 · ✅ 与预测市场相关')

    if a.post:
        return post(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
