# 币安广场自动发帖

官方 OpenAPI(非cookie自动化), 每日上限 100 帖, 零封号风险。

## 组件
- `/opt/square-post/` — 官方 skill(binance/binance-skills-hub), 用 bun 运行
- `/opt/alpha-radar/square_publish.py` — 内容组装 + 调用官方脚本
- `/etc/square-post.env` (600) — BINANCE_SQUARE_OPENAPI_KEY
- `square-scan.timer` — 每天 01:00 UTC(北京9点) 发观察池(图文)
- `square-review.timer` — 每天 13:00 UTC(北京21点) 发复盘(图文)

## Write-to-Earn 归因要点(重要)
- 只有带 **cashtag($SYMBOL)** 或图表组件的帖子, 读者点击后交易才能把手续费分成归因给作者
- 基础佣金 20%, 周榜前100 可达 50%; 每周结算, 下周四发 USDC
- 故: 所有币名自动加 cashtag(中文/特殊符号 symbol 无法成 cashtag, 自动跳过)
- 写热门/trending 币更容易被系统推荐

## 运维
- 手动发: `square_publish.py --scan` / `--review` / `--text "..."`
- 依赖: bun(已装 ~/.bun/bin/bun) + matplotlib(在 /opt/akshare-venv, 故用该 venv 的 python 运行)
- 错误码: 220009=日发帖超限, 220014=上传超限, 20002/20022=敏感词, 20013=内容过长
