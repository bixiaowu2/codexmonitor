# Stock Radar

Read-only research radar for autonomous supply chains, supply-chain disruption/substitution, and AI infrastructure. It polls public Yahoo Finance chart data, ranks the configured universe, and supports Telegram/DingTalk delivery when credentials are supplied through environment variables or secret files.

The theme labels are research categories. They do not imply orders, policy support, valuation, or future returns. The radar does not place trades.

Defaults: 5-minute market-data polling, hourly ranking, conditional event alerts with a 30-minute deduplication bucket. The initial universe is intentionally small and should be expanded only after data quality is verified.
