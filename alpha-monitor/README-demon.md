# Alpha Monitor(妖币筛选器)

基于币安 Alpha + 合约 + 链上数据的"妖币"筛选与跟踪工具。

**核心思路**:币安 Alpha 代币中,上了合约、还没上现货、链上大户高度控盘的币,在"临近现货上市"这个窗口期最容易出现暴涨暴跌。本工具筛出这类候选,并实时跟踪其"启动信号"与"回撤信号"。

> ⚠️ **免责声明**:本工具只做数据筛选与信号提示,不构成投资建议,不推荐买卖点。高控盘币风险极高,可能瞬间归零。

---

## 算法原理

### 筛选器(`demon_scan.py`)

**妖币画像**:上币安 Alpha → 又上了币安合约(USD-M 永续)→ 未上币安现货 → 合约持仓量足够 → 链上大户高度控盘。

筛选条件:
| 条件 | 数据源 | 说明 |
|---|---|---|
| 上币安 Alpha + 合约 | Binance 官方接口 | 用 `universe()` 精确匹配(官方别名/唯一符号) |
| 未上币安现货 | Binance 现货 exchangeInfo | 排除已在现货交易的 |
| 合约持仓名义 ≥ 5M USDT | `openInterestHist` | 有真实资金关注 |
| 清洗后 Top10 ≥ 40% | GoPlus `token_security` | **剔除 `is_contract=1` 的 LP 池/合约地址后**,前 10 大户持仓占比 |

**关键设计:清洗后大户集中度**

小市值币的"原始 Top10 持仓"里,LP 池/交易所/项目方合约往往占大头,看起来都很"控盘"但没区分度。本工具用 GoPlus 返回的 `is_contract` 标签,剔除这些地址,只算**真实外部大户**的集中度。这是区分"真控盘妖币"和"普通小币"的关键。

**数据可靠性校验**

GoPlus 偶发返回脏数据(如某地址 `percent > 100%`,或清洗后求和 > 100%),本工具会检测这种自相矛盾的数据并标记为"不可靠"、跳过该候选,避免误报。

### 跟踪器(`demon_track.py`)

对筛选出的命中币,实时监控两类信号:

**B. 启动信号**(出现即推 Telegram + 钉钉):
- 🚀 资金费率转负(< -0.1%):空头给多头付费,可能逼空拉升;
- 📈 合约持仓暴增(较前期 +30%):有资金进场。

**A. 回撤信号**(出现即推 Telegram + 钉钉):
- ⚠️ 价格从 3 日高点回撤 ≥ 15%:瀑布或刚开始,提示止损。

每天一次命中币状态汇总(持仓 + 费率)。

---

## 历史规律(为什么这样设计)

对 8 个历史妖币(AKE/RIVER/M/UB/RAVE/COAI/GUA/BEAT/LAB)的回溯分析发现:

1. **真妖币多是"长期横盘 → 突然垂直拉升"**:暴涨前 7 天价格几乎不动(1~2.9x),然后短时间爆发(峰值 8~110x)。所以"还没拉盘的横盘高控盘币"更值得关注。
2. **原始 Top10 没区分度**:这些币原始 Top10 都在 46%~96%,但剔除 LP 池后,真实大户集中度差异巨大(3%~95%)。只有"清洗后"高集中度才是真控盘。
3. **资金费率是"确认"信号不是"提前"信号**:负费率、持仓异动都发生在价格启动之后,不能预知,但能帮你"确认启动、及时跟上"。
4. **回撤提醒比猜顶更靠谱**:这些币见顶后回撤 -39%~-99%,从高点回撤 15% 提醒,比试图猜顶现实得多。

---

## 使用方法

### 依赖

- Python 3.10+(无第三方依赖,纯标准库)
- 币安 Alpha 雷达的 `/opt/alpha-radar` 模块(`radar.py`/`binance_official.py`/`dingtalk.py`)——用于 Binance 接口和钉钉发送

### 配置(`/etc/alpha-radar.env`)

```dotenv
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
DINGTALK_ENABLED=true
DINGTALK_WEBHOOK=xxx
DINGTALK_SECRET=xxx
DINGTALK_KEYWORD=Alpha雷达
# 可选阈值
DEMON_MIN_OI_USDT=5000000
DEMON_MIN_CLEANED_TOP10=0.40
```

### 手动运行

```bash
# 筛选
sudo python3 demon_scan.py
# 跟踪
sudo python3 demon_track.py
```

### systemd 定时运行

`demon-scan.timer`(每小时)+ `demon-track.timer`(每 30 分钟),由 systemd 管理。

---

## 数据来源

| 数据 | 来源 | 用途 |
|---|---|---|
| 币安 Alpha 名录 + 合约 | Binance 官方 API | 筛选 dual-listed |
| 合约持仓量 | Binance `openInterestHist` | 持仓名义值 |
| 清洗后大户集中度 | GoPlus `token_security` | 核心指标(带 is_contract) |
| 资金费率 | Binance `fapi/v1/fundingRate` | 启动信号 |
| 价格 | Binance `fapi/v1/klines` | 回撤计算 |

## 文件说明

- `demon_scan.py` — 筛选器:扫全量 Alpha 代币,按"清洗后大户集中度"排序,推送命中
- `demon_track.py` — 跟踪器:监控命中币的启动/回撤信号,实时推送
