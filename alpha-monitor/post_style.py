"""帖子风格模块 —— 由 11 条实测爆帖反推的写法, X 与币安广场共用。

【数据依据】2026-10-15 实测 11 条加密领域帖子, 算 赞/粉丝:

    平均行长 16.5 字 → 0.980%   @Danny_Crypton  BTC 四年周期表(17 行, 每行一个数字)
    平均行长 31.8 字 → 0.343%   @zenkaixbt      BTC $250K/ETH $8K/SOL $1K
    平均行长 48.5 字 → 0.258%   @CryptoNobler   🚨 明天是最糟的一天
    平均行长 73.7 字 → 0.057%   @CryptoPicsou   $PAPER 持仓播报  ← 全场最低

    对照: @laoyingkhq(3.2万粉) "我拿 Claude Code 改了配置…实测都能用" = 1.225%
          @WatcherGuru(490万粉) JUST IN 快讯                          = 0.089%

【六条规则】
  1. 首行是钩子, 带**具体数字或反差**, 不超一行
  2. 每行一个信息点, 行间空行 —— 短行是硬指标, 不是审美偏好
  3. 用具体数字(对比/金额/百分比), 不用形容词
  4. 第一人称("我扒了" / "我盯的" / "我实测")
  5. 结尾留开放循环 —— 不把结论说完, 让人想点进来/回复
  6. 不写长句、不播报自己的持仓

【为什么是这几条】互动率与行长呈单调关系: 行越短越高。而粉丝数与互动率**无关**
(@WatcherGuru 490万粉 0.089% < @laoyingkhq 3.2万粉 1.225%)。粉丝能买, 互动率买不来。
"""

X_LIMIT = 280          # X 加权字数上限
X_AVG_LINE = 30        # 建议平均行长(加权字), 超过就拆行
CJK_EMOJI_WEIGHT = 2


def _w(ch):
    """单个字符在 X 上的权重: CJK 与 emoji 记 2, 其余记 1。"""
    o = ord(ch)
    if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3
            or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE6F
            or 0xFF00 <= o <= 0xFF60 or 0x1F300 <= o <= 0x1FAFF):
        return CJK_EMOJI_WEIGHT
    return 1


def xlen(text):
    """X 的加权字数。中文一个字占 2, 所以 280 的上限实际约 140 个汉字。"""
    return sum(_w(c) for c in text)


def compose(head, items, tail=None, limit=X_LIMIT):
    """按风格组装帖子: 头(钩子) / 条目 / 尾(开放循环)。每段之间空一行。

    items 里每一项是**一个段落**(可以含换行), 这样删条目时不会留下孤儿行。
    X 超限时从末尾删条目直到塞得下 —— 宁可少列一个币,
    也不能让钩子被截断(被截断的钩子等于没有钩子)。
    """
    items = [i for i in items if i]
    while True:
        blocks = [head] + items + ([tail] if tail else [])
        text = '\n\n'.join(b for b in blocks if b)
        if xlen(text) <= limit or len(items) <= 1:
            return text
        items.pop()


# 币安广场: 单帖 cashtag 服务端上限 3 个(超出报 220095 Coin pair count)
SQUARE_MAX_TAGS = 3


def audit(text, limit=None, platform=None):
    """自检: 返回不符合风格的项。用于上线前检查, **只告警不阻断**(发不出去比风格差更糟)。

    platform: 'x' 会额外查 280 加权字; 'square' 会额外查 cashtag 数量上限。
    """
    warn = []
    lines = [l for l in text.split('\n') if l.strip()]
    if not lines:
        return ['空帖']
    avg = sum(xlen(l) for l in lines) / len(lines)
    if avg > X_AVG_LINE:
        warn.append(f'平均行长 {avg:.0f} 加权字 > {X_AVG_LINE}(行太长, 拆开)')
    longest = max(lines, key=xlen)
    if xlen(longest) > 60:
        warn.append(f'最长行 {xlen(longest)} 加权字, 建议拆行: {longest[:30]}…')
    if not any(c.isdigit() for c in text):
        warn.append('全帖无数字(缺具体信息)')
    if text.count('\n\n') < 2:
        warn.append('段落太少, 缺空行分隔')
    if limit and xlen(text) > limit:
        warn.append(f'超字数 {xlen(text)} > {limit}')
    if platform == 'x' and xlen(text) > X_LIMIT:
        warn.append(f'X 超限: {xlen(text)} > {X_LIMIT} 加权字')
    if platform == 'square':
        import re as _re
        tags = _re.findall(r'\$[A-Za-z0-9_]{1,15}', text)
        if len(tags) > SQUARE_MAX_TAGS:
            warn.append(f'广场 cashtag {len(tags)} 个 > 上限 {SQUARE_MAX_TAGS}(会报 220095)')
        if not tags:
            warn.append('广场帖 0 个 cashtag —— 内容挖矿赚不到(读者无从点击)')
    return warn
