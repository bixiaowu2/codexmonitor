"""全量统计：Predict.fun 上有多少个「独立二元非体育」市场，其中 No ≤ 0.65 的有几个。

对应 nothing-ever-happens 的五条筛选（见 GitHub sterlingcrispin/nothing-ever-happens）:
  1. outcomes 恰好是 {Yes, No}
  2. 不含体育/电竞关键词
  3. 在 N 个月内结束
  4. standalone: 同一 event 下只有 1 个 market
  5. 非 sports 类型

为了省 API 调用: 先用 list 的字段做**预筛**(分类/子市场数/标签), 只对幸存者拉 detail 看 outcomes。
"""
from __future__ import annotations

import json, subprocess, sys, time
from collections import Counter

BAW = '/home/ubuntu/.bun/bin/baw'
PAGE = 100
MAX_PAGES = 20
EXCLUDE_L1 = {'sports', 'esports'}
EXCLUDE_KW = ('football', 'soccer', 'nba', 'nfl', 'mlb', 'nhl', 'tennis', 'golf', 'cricket',
              'rugby', 'olympics', 'world series', 'super bowl', 'march madness', 'stanley cup',
              'world cup', 'ncaa', 'college', 'esports', 'e-sports', 'table tennis',
              'chess', 'pickleball', 'boxing', 'ufc', 'f1', 'formula 1')
MAX_MONTHS = 24
NO_CAP = 0.65
MIN_LIQ = 50000


def call(args, timeout=60):
    try:
        r = subprocess.run([BAW] + args, capture_output=True, text=True, timeout=timeout)
        return json.loads(r.stdout) if r.stdout.strip().startswith('{') else None
    except Exception:
        return None


def listing():
    out, off = [], 0
    for _ in range(MAX_PAGES):
        d = call(['prediction', 'market', 'list', '--json', '--limit', str(PAGE), '--offset', str(off)])
        if not d:
            break
        rows = (d.get('data') or {}).get('marketTopics') or []
        if not rows:
            break
        out.extend(rows)
        if not (d.get('data') or {}).get('hasMore'):
            break
        off += PAGE
        time.sleep(0.3)
    return out


def prefilter(t):
    """用 list 字段预筛, 便宜的检查先做。"""
    l1 = ((t.get('l1CategoryNames') or [''])[0] or '').lower()
    if l1 in EXCLUDE_L1:
        return False
    blob = ' '.join([str(t.get('title') or ''), str(t.get('question') or ''),
                     ' '.join(map(str, t.get('tags') or []))]).lower()
    if any(k in blob for k in EXCLUDE_KW):
        return False
    ms = t.get('markets') or []
    if len(ms) != 1:                      # standalone 代理: 同一 event 只有 1 个子市场
        return False
    return True


def main():
    print('拉取全市场列表…', flush=True)
    tops = listing()
    print(f'  收到 {len(tops)} 个市场', flush=True)
    print('  分类:', dict(Counter((x.get('l1CategoryNames') or ['?'])[0] for x in tops)), flush=True)

    cand = [t for t in tops if prefilter(t)]
    print(f'\n预筛后(非体育+非电竞+单子市场): {len(cand)} 个', flush=True)

    results = []
    for i, t in enumerate(cand, 1):
        tid = t.get('marketTopicId')
        d = call(['prediction', 'market', 'detail', '--marketTopicId', str(tid), '--json'], timeout=45)
        if not d:
            continue
        data = d.get('data') or {}
        ms = data.get('markets') or []
        if len(ms) != 1:
            continue
        m = ms[0]
        outs = m.get('outcomes') or []
        names = {str(o.get('name') or '').strip().lower() for o in outs}
        if names != {'yes', 'no'}:
            continue
        no = next((o for o in outs if str(o.get('name')).strip().lower() == 'no'), None)
        yes = next((o for o in outs if str(o.get('name')).strip().lower() == 'yes'), None)
        liq = float(data.get('liquidity') or 0)
        results.append({
            'id': tid, 'title': str(data.get('title'))[:50],
            'l1': (data.get('l1CategoryNames') or ['?'])[0],
            'no': float(no.get('price') or 0) if no else None,
            'yes': float(yes.get('price') or 0) if yes else None,
            'liq': liq,
        })
        if i % 10 == 0:
            print(f'  …已查 {i}/{len(cand)}, 合格 {len(results)}', flush=True)
        time.sleep(0.25)

    print(f'\n合格(独立二元非体育): {len(results)} 个')
    if not results:
        return
    print('\n=== 全部合格市场 ===')
    print(f"{'No价':>7}{'Yes价':>7}{'流动性':>12}  标的")
    for r in sorted(results, key=lambda x: (x['no'] if x['no'] is not None else 9)):
        print(f"{r['no'] if r['no'] is not None else -1:>7.3f}{r['yes'] if r['yes'] is not None else -1:>7.3f}"
              f"{r['liq']:>12,.0f}  [{r['l1']}] {r['title']}")

    tradable = [r for r in results if r['no'] is not None and r['no'] <= NO_CAP and r['liq'] >= MIN_LIQ]
    print(f'\n=== 符合策略(No≤{NO_CAP} 且 流动性≥{MIN_LIQ:,}): {len(tradable)} 个 ===')
    for r in tradable:
        print(f"  No {r['no']:.3f} (赢则 +{(1/r['no']-1)*100:.0f}%)  流动性 {r['liq']:,.0f}  {r['title']}")

    json.dump(results, open('/tmp/pm_eligible.json', 'w'), ensure_ascii=False, indent=1)
    print('\n已写 /tmp/pm_eligible.json')


if __name__ == '__main__':
    main()
