# 四套雷达数据来源与可用性

更新时间：2026-09-27。本文区分“可复用的方法代码”和“可作为信号依据的实时数据”。GitHub 星数只表示项目关注度，不表示数据准确性、稳定性或商业使用许可。

## 来源矩阵

| 雷达 | 当前主要来源 | 可提供的字段 | 当前限制 | 使用规则 |
| --- | --- | --- | --- | --- |
| X-monitor | 浏览器会话抓取公开 X 页面 | 帖子正文、作者、发布时间、链接 | 受登录、页面结构、访问限制影响；没有付费 API 的完整历史保证 | 账号级记录错误；帖子只能作为事件证据，不能单独触发高置信买入 |
| 币安 Alpha | Binance Alpha 名录、Alpha K线/ticker、USD-M 合约公开接口、DexScreener、GoPlus | Alpha 上市、合约交集、价格、成交量、资金费率、持仓/安全字段（可得时） | ticker 过期、合约身份和链上身份需对齐；解锁和关联钱包常无统一公开源 | 按链+合约地址匹配；过期或缺失报价不能触发买入/止盈止损 |
| Meme | GeckoTerminal、DexScreener、GoPlus；各链 RPC 是后续增强方向 | 池子、流动性、成交、价格变化、基础安全字段 | 429/backoff；Arc、Stable、X Layer 等链的备用源不足；安全字段可能 unknown | 429 记为数据质量失败，不当作“无机会”；unknown 降级，不当作安全通过 |
| 股票 | Yahoo Finance chart（当前只读主源） | 5分钟K线、成交量、上一交易日变化、市场新鲜度 | 单一行情源；休市和延迟必须区分；财报、公告、产业链关系还未接入 | A股、港股、美股独立排名；无新鲜报价时只发状态，不伪造排名 |

## GitHub 可复用项目

| 项目 | 当前星数（2026-09-27 API 读取） | 许可证 | 适合借鉴的部分 |
| --- | ---: | --- | --- |
| [ccxt/ccxt](https://github.com/ccxt/ccxt) | 44,172 | MIT | 交易所适配、统一错误和限流模型 |
| [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot) | 20,229 | Apache-2.0 | 连接器抽象、纸面运行、订单生命周期和运行架构 |
| [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | 54,831 | GPL-3.0 | 时间切分、费用建模、回测和前瞻验证 |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | 73,499 | 按引入文件审查 | Provider 抽象、来源追踪和多市场数据模型 |
| [DefiLlama/DefiLlama-Adapters](https://github.com/DefiLlama/DefiLlama-Adapters) | 1,260 | 按文件和仓库条款审查 | 链上协议/TVL 适配思路和链覆盖维护 |

GitHub 上直接声称“聪明钱包”“百倍币”且缺少可复现历史快照的项目，不能直接作为评分数据。可借鉴代码结构，但必须自己保存地址、时间、原始响应、数据年龄和失败样本。

## 优先补强顺序

1. Meme：为每条链增加一个独立备用报价源或 RPC 读取，先 shadow-run；重点解决 429 和覆盖不足。
2. 股票：增加第二只读行情源，并接入公告/财报原始链接；先做交叉校验，不改变阈值。
3. Alpha：保存名录、合约身份、链上地址和历史报价快照，减少 ticker 过期造成的前瞻缺失。
4. X-monitor：继续账号级错误分类和持久 outbox；把帖子与代币地址、链和时间窗口做可审计关联。

所有来源都应记录 provider、抓取时间、数据年龄、覆盖范围、原始错误和许可证/商业限制。数据缺失不能解释为没有机会，也不能用事后最高价替代可交易收益。
