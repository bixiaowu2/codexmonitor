# Stock Radar

Read-only research radar for autonomous supply chains, supply-chain disruption/substitution, and AI infrastructure. It polls public Yahoo Finance chart data, ranks the configured universe, and supports Telegram/DingTalk delivery when credentials are supplied through environment variables or secret files.

The theme labels are research categories. They do not imply orders, policy support, valuation, or future returns. The radar does not place trades.

Defaults: 5-minute market-data polling, hourly ranking, conditional event alerts with a 30-minute deduplication bucket. The initial universe is intentionally small and should be expanded only after data quality is verified.

Rankings are sent separately by listing market, with up to five stocks per market. The current universe includes 10 China A-shares, 7 US-listed instruments (including ASML, ARM and TSM), and 1 Korean-listed instrument. Issuer domicile is preserved as context and does not determine the trading-market group. Each market has independent hourly queue keys and candidate limits.

Data uses completed 5-minute candles. Stale quotes do not generate rankings or instant alerts; closed markets therefore remain quiet. Public quotes may be delayed. Theme labels are a manually configured research universe: policy/news/financial verification is not implemented in this version.

The universe includes a Hong Kong subgroup under Chinese stocks. Hong Kong candidates are labeled 港股 and ranked within the Chinese-stock market feed; current coverage focuses on semiconductor manufacturing/equipment, AI cloud/models, optical sensors, and intelligent devices. Inclusion is a monitoring hypothesis, not a recommendation or a claim of 100x potential.

## 前瞻结果账本

即时信号始终写入 `forward_tracks`，记录 1h/6h/24h/7d 的后续收益、峰值和回撤；通知开关不影响研究样本。


## 前瞻账本口径修正（forward-v2，2026-09-27）

1h/6h/24h/7d按自然时间计算，仅接受到期后30分钟内首次有效观测；超过窗口的价格不得回填更早的收益端点。持续没有报价的资产在窗口关闭后明确记为missing。旧账本中超时观测从有效收益统计剔除，原数据保留在`excluded_observation`内，不能据此宣称盈利能力提升。

新增样本记录按采样顺序计算的峰值到后续低点最大回撤；`trough_return`仍为相对入场价的最低收益，二者不能混称。旧轨迹缺少完整价格路径，`max_drawdown`保持NULL，不能事后伪造。样本仍未扣手续费/滑点，峰值不是实现收益。尚无独立行情基准计算发现提前量，`lead_time`明确为null。

`forward.horizons`分别列出到期、有效、缺失、待观察数。`complete`表示所有端点均已结案（可含missing），不代表数据齐全。周末休市的股票自然时间端点可缺失；不拿下次开市价冒充周末端点。股票信号研究账本独立于通知开关，观测使用行情时间以拒绝重复旧报价，市场标签保留港股/A股子分类。

发现源样本轮换、接口限流以及资产退出候选仍可造成跟踪缺报价；此更新纠正统计口径，不宣称已解决报价覆盖。后续需要独立的已发信号行情跟踪队列和备用报价源。策略阈值不变。
