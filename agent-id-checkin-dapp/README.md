# Agent ID 每日签到 dApp（本机测试架子）

状态：2026-11-19 夜间已上线；合约 `0x8d314451f99bffdac9d2d51c683a5b9aecc1ecb7`，dApp https://agent-id-checkin-dapp.bixiaowu.workers.dev ；公开仓库 bixiaowu2/codexmonitor/main（HEAD `ea5627c97b161a844ea06c702b62c106924c8386`），Cloudflare Version `deddbd17`。用户已批公开仓库、部署 gas、对外发布三项（d428）；home 不代签 gas、不碰资金线、不收集任何凭据。BscScan 源码页已由用户目视确认“Exact Match”。真实签到由持 Agent ID 的用户本人在钱包内签名并付 gas，home 不代做；当前链上交互地址 4/30，缺口 26 由 lead 协调社区渠道。

约定：静态数据读 `data/latest.json` 与 `data/daily/YYYY-MM-DD.json`，顶层 `date`/`generated_at`，`sections.stocks`/`meme`/`x`。钱包连接走浏览器注入，不网页扫码登 Agentic Wallet。`tools/sync_radar_export.py` 已改为环境变量/命令行参数，不把本机 SSH 私钥路径写入公开仓库；发布前仍运行 `scripts/scan_secrets.py --root . --strict`。签到状态链上读，数据链下读；对外口径只说“签到后展示完整数据面板”，不宣称加密解锁。

本地验证：`python3 tools/sync_radar_export.py --check`、`node --check app.js`、`node tests/leaderboard-chain.test.mjs`（6/6 PASS）、`python3 scripts/scan_secrets.py --root . --strict`（0 命中）。合约接口 `checkIn(uint256 tokenId)`、`hasCheckedInToday(tokenId)`、`streakOf(tokenId)`、`lastCheckInDay(tokenId)`，事件 `CheckedIn(uint256 indexed tokenId, uint256 indexed day, uint256 streak)`；`tokenId = uint256(keccak256(bytes(label)))`，不是 namehash、也不是 `abi.encode(string)`。
