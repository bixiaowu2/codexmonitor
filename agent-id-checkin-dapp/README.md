# Agent ID 每日签到 dApp（本机测试架子）

状态：2026-11-19 上午已建本地 git 仓库并验证可测版；用户已批公开仓库、部署 gas、对外发布三项（d428）。home 仍不代签 gas、不碰资金线、不收集任何凭据。合约地址/RPC 就绪后注入 config；公开仓库推送需要合法认证，当前阻塞已报 lead。

约定：静态数据读 `data/latest.json` 与 `data/daily/YYYY-MM-DD.json`，顶层 `date`/`generated_at`，`sections.stocks`/`meme`/`x`。钱包连接走浏览器注入，不网页扫码登 Agentic Wallet。`tools/sync_radar_export.py` 已改为环境变量/命令行参数，不把本机 SSH 私钥路径写入公开仓库；发布前仍运行 `scripts/scan_secrets.py --root . --strict`。签到状态链上读，数据链下读；对外口径只说“签到后展示完整数据面板”，不宣称加密解锁。

本地验证：`python3 tools/sync_radar_export.py --check`、`node --check app.js`、`python3 scripts/scan_secrets.py --root . --strict`。合约接口 `checkIn(uint256 tokenId)`、`hasCheckedInToday(tokenId)`、`streakOf(tokenId)`、`lastCheckInDay(tokenId)`，事件 `CheckedIn(tokenId, day, streak)`，`tokenId=keccak256(名字)`。
