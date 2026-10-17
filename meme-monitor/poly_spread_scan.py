"""只读跨平台价差扫描：Predict.fun vs Polymarket。

【目的】不碰私钥、不下单 —— 先证明"两个平台之间到底有没有价差"。
        如果一周都抓不到几次、扣 2% 手续费后为负，那套利这条路直接关闭，省得折腾签名。

【套利条件】（两平台都是二元市场，份额结算为 $1 或 $0）
  A) Predict 买 YES + Polymarket 买 NO   < 1  →  锁定利润
  B) Predict 买 NO  + Polymarket 买 YES  < 1  →  锁定利润
  扣费后仍 < 1 才算真机会（Predict taker 2%，Polymarket 视市场而定）。

【价格口径】
  Predict: outcome.price（报价，不是严格 ask —— 用于可行性判断足够）
  Polymarket: bestAsk = YES 的卖一；NO 的卖一 ≈ 1 - YES 的买一（complete-set 关系）

【匹配】按 question 的 token Jaccard 相似度，阈值 0.62；输出按相似度排序供人工复核。

用法:
    poly_spread_scan.py            # 扫一轮
    poly_spread_scan.py --top 40   # 只看最像的 40 对
"""
from __future__ import annotations

import json, re, subprocess, sys, time, urllib.request
from pathlib import Path

BAW = '/home/ubuntu/.bun/bin/baw'
OUT = Path('/var/lib/meme-radar/poly_spread.json')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0 Safari/537.36')
PREDICT_FEE = 0.02
MIN_SIM = 0.62
STOP = {'will', 'the', 'a', 'an', 'of', 'in', 'on', 'by', 'to', 'be', 'is', 'are',
        'before', 'after', 'and', 'or', 'for', 'at', 'from', 'that', 'this', 'it'}


def norm(text):
    words = re.findall(r'[a-z0-9]+', str(text or '').lower())
    return {w for w in words if w not in STOP and len(w) > 1}


def jaccard(a, b):
    return len(a & b) / len(a | b) if (a and b) else 0.0


def baw(args, timeout=120):
    try:
        r = subprocess.run([BAW] + args, capture_output=True, text=True, timeout=timeout)
        i = (r.stdout or '').find('{')
        return json.loads(r.stdout[i:]) if i >= 0 else None
    except Exception:
        return None


def http_json(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def predict_markets(limit_pages=20, page=100):
    """拉 Predict 的全部市场 + 每个市场的 Yes/No 报价。"""
    topics, off = [], 0
    for _ in range(limit_pages):
        d = baw(['prediction', 'market', 'list', '--json', '--limit', str(page), '--offset', str(off)])
        if not d:
            break
        rows = (d.get('data') or {}).get('marketTopics') or []
        if not rows:
            break
        topics.extend(rows)
        if not (d.get('data') or {}).get('hasMore'):
            break
        off += page
        time.sleep(0.25)
    out = []
    for t in topics:
        ms = t.get('markets') or []
        if len(ms) != 1:
            continue
        d = baw(['prediction', 'market', 'detail', '--marketTopicId', str(t.get('marketTopicId')), '--json'])
        if not d:
            continue
        data = d.get('data') or {}
        sub = (data.get('markets') or [])
        if len(sub) != 1:
            continue
        outs = sub[0].get('outcomes') or []
        names = {str(o.get('name') or '').strip().lower() for o in outs}
        if names != {'yes', 'no'}:
            continue
        yes = next((o for o in outs if str(o.get('name')).strip().lower() == 'yes'), None)
        no = next((o for o in outs if str(o.get('name')).strip().lower() == 'no'), None)
        if not yes or not no:
            continue
        out.append({
            'id': t.get('marketTopicId'), 'q': str(data.get('title') or t.get('title') or ''),
            'yes': float(yes.get('price') or 0), 'no': float(no.get('price') or 0),
            'liq': float(data.get('liquidity') or 0), 'tokens': norm(data.get('title') or t.get('title')),
        })
        time.sleep(0.2)
    return out


def poly_markets(pages=6, page=500):
    """拉 Polymarket 活跃市场（按成交量降序）。"""
    out, off = [], 0
    for _ in range(pages):
        url = ('https://gamma-api.polymarket.com/markets?limit=%d&offset=%d'
               '&active=true&closed=false&archived=false&order=volumeNum&ascending=false'
               % (page, off))
        try:
            rows = http_json(url)
        except Exception:
            break
        if not isinstance(rows, list) or not rows:
            break
        for m in rows:
            outs = m.get('outcomes')
            if isinstance(outs, str):
                try:
                    outs = json.loads(outs)
                except Exception:
                    continue
            if not outs or len(outs) != 2:
                continue
            labels = {str(o).strip().lower() for o in outs}
            if labels != {'yes', 'no'}:
                continue
            ba, bb = m.get('bestAsk'), m.get('bestBid')
            try:
                ba = float(ba) if ba is not None else None
                bb = float(bb) if bb is not None else None
            except (TypeError, ValueError):
                continue
            if ba is None or bb is None:
                continue
            out.append({
                'id': m.get('id'), 'q': str(m.get('question') or ''),
                'yes_ask': ba, 'no_ask': round(1 - bb, 4),      # complete-set 关系
                'vol': float(m.get('volumeNum') or 0), 'tokens': norm(m.get('question')),
            })
        off += page
        time.sleep(0.3)
    return out


def main():
    top_n = 40
    if '--top' in sys.argv:
        top_n = int(sys.argv[sys.argv.index('--top') + 1])

    print('拉 Predict 市场并逐个取报价…', flush=True)
    p_ = predict_markets()
    print(f'  Predict 合格二元市场: {len(p_)}', flush=True)
    print('拉 Polymarket 活跃市场…', flush=True)
    m_ = poly_markets()
    print(f'  Polymarket 二元市场: {len(m_)}', flush=True)

    pairs = []
    for a in p_:
        best, bs = None, 0.0
        for b in m_:
            s = jaccard(a['tokens'], b['tokens'])
            if s > bs:
                bs, best = s, b
        if best and bs >= MIN_SIM:
            # A) Predict YES + Poly NO
            arb_a = a['yes'] + best['no_ask']
            # B) Predict NO + Poly YES
            arb_b = a['no'] + best['yes_ask']
            pairs.append({
                'sim': round(bs, 3),
                'predict': a['q'][:70], 'poly': best['q'][:70],
                'p_yes': a['yes'], 'p_no': a['no'],
                'm_yes_ask': best['yes_ask'], 'm_no_ask': best['no_ask'],
                'arb_a': round(arb_a, 4), 'arb_b': round(arb_b, 4),
                'edge_a': round((1 - arb_a) * 100, 2), 'edge_b': round((1 - arb_b) * 100, 2),
                'p_liq': a['liq'], 'm_vol': best['vol'],
            })

    pairs.sort(key=lambda x: -x['sim'])
    print(f'\n匹配上的对: {len(pairs)}')
    print(f"\n{'相似':>5}{'A边%':>8}{'B边%':>8}  Predict / Polymarket")
    print('-' * 100)
    for x in pairs[:top_n]:
        print(f"{x['sim']:>5.2f}{x['edge_a']:>8.1f}{x['edge_b']:>8.1f}  "
              f"{x['predict'][:44]:<46} | {x['poly'][:34]}")

    # 扣费后仍有空间的（Predict taker 2% 每条腿；Polymarket 视市场，先按 0 算）
    real = [x for x in pairs if x['arb_a'] + PREDICT_FEE < 1 or x['arb_b'] + PREDICT_FEE < 1]
    print(f'\n=== 扣 {PREDICT_FEE*100:.0f}% 手续费后仍有空间: {len(real)} 对 ===')
    for x in real:
        side = 'A(Predict YES + Poly NO)' if x['arb_a'] + PREDICT_FEE < 1 else 'B(Predict NO + Poly YES)'
        cost = x['arb_a'] if 'A' in side[0] else x['arb_b']
        print(f"  {side}  成本 {cost:.4f} → 利润 {100*(1-cost-PREDICT_FEE):.1f}%  "
              f"[{x['sim']:.2f}] {x['predict'][:40]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(pairs, ensure_ascii=False, indent=1))
    print(f'\n已写 {OUT}')


if __name__ == '__main__':
    main()
