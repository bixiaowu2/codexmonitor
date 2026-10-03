# Binance Skills 适用性（2026-10-03）

官方目录：https://www.binance.com/zh-CN/skills
技能源码：https://github.com/binance/binance-skills-hub

| 技能 | Alpha 雷达 | Meme 雷达 | 股票雷达 | 当前处理 |
| --- | --- | --- | --- | --- |
| Alpha 官方 token list / ticker / K 线 | 合约身份和 Alpha 现货报价 | 仅交叉核对 BSC 上的同一合约 | 不适用 | Alpha 已接入；报价必须有有效时间戳，过期保持缺失 |
| `query-token-info` | BSC 链上价格、持有人和流动性作独立参考 | BSC、Solana、Base、Ethereum 的链上详情 | 不适用 | Meme 已接入有界实时查询；链上价不能冒充 Alpha 现货价 |
| `query-token-audit` | BSC 合约风险辅助证据 | 支持链的审计辅助证据 | 不适用 | Meme 已接入；无结果或接口业务错误不算已审计，也不证明可卖 |
| `crypto-market-rank` | Alpha 榜单、聪明钱流入候选来源 | 热度、Meme 和资金流候选来源 | “Stock”榜仅指代币化证券 | 待做前瞻记录和独立验证；不直接改变买入分 |
| `meme-rush` | 无直接用途 | BSC、Solana、Base 新币与迁移池发现 | 不适用 | 待验证新币地址、流动性与审计覆盖，再考虑接入发现层 |
| `trading-signal` / `binance-trading-signal` | BSC 聪明钱事件参考 | BSC、Solana 聪明钱买卖事件 | 不适用 | 待验证时间戳、钱包去重及事后表现，暂不直接加分 |
| `binance-wallet-tracker` / `query-address-info` / `binance-leaderboard` | BSC 钱包归因线索 | 钱包质量、关联关系和持仓线索 | 不适用 | 只能作可核验的观察证据；榜单身份不等于真实盈利能力 |
| `binance-tokenized-securities-info` | 不适用 | 不适用 | 仅 Ondo 等链上证券代币，不能替代 A 股、港股或美股交易所行情 | 与股票主排名隔离；目前不接入 |

交易、自动跟单、钱包签名、付款、提现和发帖类技能不属于当前只读雷达。额外链 Arc、Stable、Robinhood、X Layer 不在上述 Web3 查询技能列出的四条支持链内，继续走各自数据源并显示覆盖缺口。

接入门槛：按链和精确合约地址匹配，记录来源及采集时间；区分 HTTP 成功与业务成功；缺字段、过期或限流时维持未知；新排行/信号先留前瞻样本，再用独立时间段和资产分组验证增量效果。任何链上证券代币价格都不能覆盖真实股票报价。
