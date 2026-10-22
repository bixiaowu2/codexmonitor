"""帖子风格模块 —— 由实测爆帖反推的写法, X 与币安广场共用。

【数据依据】2026-10-15 初测 11 条, 2026-10-18 复测 13 条加密领域帖子,
算 点赞/粉丝数:

    行长 16.5~41.8 字 → 0.42%~1.22%   @Danny_Crypton / @laoyingkhq / @Naive_BNB
    行长 53~73 字     → 0.06%~0.26%   @CryptoNobler / @CryptoPicsou / @WatcherGuru

【两条已被复测修正的结论 —— 别再按旧版优化】
  1. 「行长越短越好」**不是单调关系**。复测发现互动率最低的两条帖子
     (@waveking1314 0.008%、@NFTCPS 0.003%) 行长恰恰最短(23.5 / 28 字)。
     长行内容普遍偏低只是**弱趋势**, 不是规律。
     X_AVG_LINE = 30 作为**可读性**阈值保留(中文一行约 15 字), 但别再声称单调关系。
  2. 「粉丝数与互动率无关」**框架有误** —— 实为**公认的负相关**(账号越大互动率越低)。
     所以 **点赞/粉丝数 不能跨账号比较**: 小号天然显得高, 会把你导向小号。
     有效做法是跟**同一账号自己的历史平均**比。

【六条规则】
  1. 首行是钩子, 带**具体数字或反差**, 不超一行
  2. 每行一个信息点, 行间空行 —— 短行便于扫读
  3. 用具体数字(对比/金额/百分比), 不用形容词
  4. 第一人称("我扒了" / "我盯的" / "我实测")
  5. 结尾留开放循环 —— 不把结论说完, 让人想点进来/回复
  6. 不写长句、不播报自己的持仓

【三条数据一致性检查】2026-10-20 加, 见 05-雷达内容优化/变体池.md 坑3。
  audit 只看文本, 所以这三条是**兜底** —— 真正的保证在数据侧
  (变体池按「数据形状」决定哪些变体成立)。兜底只抓明确矛盾, 不做主观判断。
  A. 零计数自相矛盾(「0 个加仓」) —— 模板拿到空列表还照写
  B. 数值被舍入成 0(「0.0M$」) —— 单位选错, 该用 40K$
  C. 状态词与数据矛盾(「比较安静」+ 帖内 +433%) —— 自打脸
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
    # 合约地址行不参与行长计算 —— 0x... 无法折行，算进去是误报
    import re as _re0
    _addr = _re0.compile(r'0x[0-9a-fA-F]{20,}')
    _body = [l for l in lines if not _addr.search(l)]
    _calc = _body or lines
    avg = sum(xlen(l) for l in _calc) / len(_calc)
    if avg > X_AVG_LINE:
        warn.append(f'平均行长 {avg:.0f} 加权字 > {X_AVG_LINE}(行太长, 拆开)')
    longest = max(_calc, key=xlen)
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
    # --- 三条最影响观感的检查(2026-10-18 加) ---
    if not any(k in text for k in ('我', '咱', '盯的', '扒了', '实测')):
        warn.append('全帖无第一人称(规则4)')
    # 排除「非正文结尾」的段 —— 免责声明和纯标签行都不算结尾。
    # 标签（#xxx / $xxx）按惯例放最后，但它不是正文的收尾；
    # 不排除的话，正文里明明有问句也会误报「结尾无开放循环」。
    import re as _re3
    _TAGONLY = _re3.compile(r'^[\s#＃$＄\w\u4e00-\u9fff]+$')

    def _is_real_end(b):
        b = b.strip()
        if not b or '不构成投资建议' in b:
            return False
        # 整段只有标签/币种/空白 → 不是正文结尾
        words = [w for w in b.split() if w]
        if words and all(w.startswith(('#', '＃', '$', '＄')) for w in words):
            return False
        return True

    _last_block = [b for b in text.split('\n\n') if _is_real_end(b)]
    if _last_block and ('？' not in _last_block[-1] and '?' not in _last_block[-1]):
        warn.append('结尾无开放循环/问句(规则5)')
    _first = text.split('\n')[0].strip()
    if _first.startswith(('🚀', '⚠️', '💰', '📈', '📊', '🎯')):
        warn.append('首行是标签不是钩子(规则1)')

    # --- 三条数据一致性检查(2026-10-20 加, 见 05-雷达内容优化/变体池.md 坑3) ---
    import re as _re2
    # A. 零计数自相矛盾 —— 模板拿到了空列表还照写
    #    (「钱在换仓 0 个加仓 · 1 个撤退」这种)
    #    只列动作词, 不误伤「0 个标的变动超过 ±5%」这种正常陈述
    if _re2.search(r'(?<![\d.])0\s*(?:个|人|笔|条|次)\s*'
                   r'(?:加仓|撤退|买入|卖出|地址|信号|扫货|同时)', text):
        warn.append('出现「0 个/人/笔 动作」—— 数据形状不匹配, 该变体不该用')
    # B. 数值被舍入成 0 —— 单位选错
    #    (40000/1e6 = 0.04, 用 .1f 就变成 0.0M$)
    if _re2.search(r'(?<![\d.])0\.0+\s*[MKBT]?\$', text):
        warn.append('出现 0.0M$ 这类被舍入成 0 的数值 —— 换更小单位(如 40K$)')
    # C. 状态词与数据矛盾 —— 「比较安静」的帖子里出现 +433%
    #    只查「涨」不查「跌」: 平静描述与暴跌并存不算自相矛盾
    if any(k in text for k in ('安静', '平稳', '没动', '没信号', '很轻',
                               '不动', '没有大资金', '基本没变')):
        _big = [float(x) for x in _re2.findall(r'\+(\d+(?:\.\d+)?)%', text)]
        if _big and max(_big) >= 50:
            warn.append(f'说「安静/平稳」但帖内有 +{max(_big):.0f}% —— 自相矛盾')
        _cnt = [int(x) for x in _re2.findall(r'(\d+)\s*(?:人|笔|个地址)', text)]
        if _cnt and max(_cnt) >= 10:
            warn.append(f'说「安静/平稳」但帖内有 {max(_cnt)} 人/笔 —— 自相矛盾')
    return warn
