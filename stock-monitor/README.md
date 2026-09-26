# Stock Radar

Read-only research radar for autonomous supply chains, supply-chain disruption/substitution, and AI infrastructure. It polls public Yahoo Finance chart data, ranks the configured universe, and supports Telegram/DingTalk delivery when credentials are supplied through environment variables or secret files.

The theme labels are research categories. They do not imply orders, policy support, valuation, or future returns. The radar does not place trades.

Defaults: 5-minute market-data polling, hourly ranking, conditional event alerts with a 30-minute deduplication bucket. The initial universe is intentionally small and should be expanded only after data quality is verified.

Rankings are sent separately by listing market, with up to five stocks per market. The current universe includes 10 China A-shares, 7 US-listed instruments (including ASML, ARM and TSM), and 1 Korean-listed instrument. Issuer domicile is preserved as context and does not determine the trading-market group. Each market has independent hourly queue keys and candidate limits.

Data uses completed 5-minute candles. Stale quotes do not generate rankings or instant alerts; closed markets therefore remain quiet. Public quotes may be delayed. Theme labels are a manually configured research universe: policy/news/financial verification is not implemented in this version.
