"""扩大股票候选池: 用东财概念板块把「AI 产业链」整块拉下来。

现状: STOCK_UNIVERSE 是两份**手挑清单**合计 134 只(A股 90 / 港股 23 / 美股 21)。
用户关注 AI 产业链, 想扩大覆盖。

做法: 拉东财**全部**概念板块(分页) → 按关键词筛 AI 产业链相关 → 拉每个板块的成分股 → 去重合并。
输出到**新文件** `universe_ai_expanded.json`, 不动原来两份手挑清单 ——
由用户决定是否把它加进 STOCK_UNIVERSE。

⚠️ 成本提醒: 雷达每轮要拉全部 universe 的行情。
   134 只 ≈ 27s(Yahoo 0.2s/只); 加到 500 只 ≈ 100s; 1000 只 ≈ 200s。
   雷达间隔 300s, 所以**上限约 700–800 只**, 再多就会挤掉轮次。

用法:
    expand_universe.py --scan            # 只看能扩到多少(不写文件)
    expand_universe.py --build --limit 400   # 生成新 universe 文件
"""
from __future__ import annotations

import json, sys, time, urllib.request
from pathlib import Path

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
OUT = Path('/opt/stock-radar/universe_ai_expanded.json')

# AI 产业链相关的板块名关键词(宁宽勿窄, 后面还有去重和上限)
KEYWORDS = ('算力', '芯片', '半导体', '存储', '光模块', 'CPO', '液冷', '数据中心', '人工智能',
            'AI', '大模型', '服务器', 'GPU', '光通信', '光纤', 'PCB', '封装', '先进封装',
            '数据要素', '信创', '国产', '机器人', '智能', '通信技术', '消费电子', '光刻',
            '软件', '计算机', '云计算', '网络安全', '数字经济')


# 东财按 IP 限流: 实测连发几次就全站 502。所以退避 + 限速, 别把它打爆。
_RETRY = (1.5, 4.0, 10.0)
_SPACING = 0.8


def get_json(url, timeout=25):
    """带退避的 GET。东财 502 = 被限流, 等一下再来, 别硬刚。"""
    last = None
    for attempt in range(len(_RETRY) + 1):
        time.sleep(_SPACING)
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            last = e
            if attempt < len(_RETRY):
                time.sleep(_RETRY[attempt])
    raise last


def all_boards():
    """拉全部概念板块(分页, 每页 100)。"""
    out, pn = [], 1
    while pn <= 20:
        url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1&fltt=2&invt=2'
               '&fid=f12&fs=m:90+t:3&fields=f12,f14,f104' % pn)
        try:
            d = get_json(url)
        except Exception:
            break
        rows = ((d.get('data') or {}).get('diff') or [])
        if not rows:
            break
        out.extend(rows)
        if len(rows) < 100:
            break
        pn += 1
    return out


def board_cons(code, cap=300):
    """拉一个板块的成分股(A股)。"""
    url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=%d&po=1&np=1&fltt=2&invt=2'
           '&fid=f3&fs=b:%s&fields=f12,f14,f13' % (cap, code))
    try:
        d = get_json(url)
    except Exception:
        return []
    return ((d.get('data') or {}).get('diff') or [])


def to_symbol(code, market):
    """东财的 6 位代码 → Yahoo/雷达用的符号。f13: 0=深 1=沪"""
    code = str(code)
    if market == 1 or code.startswith(('6', '9')):
        return code + '.SS'
    return code + '.SZ'


def main():
    scan_only = '--scan' in sys.argv
    limit = 400
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])

    print('拉取东财概念板块…')
    boards = all_boards()
    print(f'  共 {len(boards)} 个板块')
    hit = [b for b in boards if any(k in str(b.get('f14')) for k in KEYWORDS)]
    print(f'  其中 AI 产业链相关 {len(hit)} 个:')
    for b in hit:
        print(f"    {b.get('f12')}  {b.get('f14'):<20} {b.get('f104')}家")

    print('\n拉取各板块成分股…')
    pool = {}          # symbol -> {name, boards:[...]}
    for b in hit:
        cons = board_cons(b.get('f12'))
        n0 = len(pool)
        for c in cons:
            sym = to_symbol(c.get('f12'), c.get('f13'))
            if sym not in pool:
                pool[sym] = {'symbol': sym, 'name': c.get('f14'), 'theme': 'AI产业链·' + str(b.get('f14')),
                             'boards': []}
            pool[sym]['boards'].append(str(b.get('f14')))
        print(f"  {b.get('f14'):<20} {len(cons):>4}家 → 新增 {len(pool)-n0:>4} (累计 {len(pool)})")
        time.sleep(0.3)

    print(f'\n合并去重后共 {len(pool)} 只 A股')

    if scan_only:
        print('(--scan: 不写文件)')
        return

    items = sorted(pool.values(), key=lambda x: x['symbol'])[:limit]
    # 与现有手挑清单区分: 这里只含 A股, 且带 theme 便于 stock_scan 分组
    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n已写 {OUT} ({len(items)} 只, 上限 {limit})')
    print('要把池子扩大, 在 /etc/stock-radar.env 的 STOCK_UNIVERSE 后面追加这个文件即可。')


if __name__ == '__main__':
    main()
