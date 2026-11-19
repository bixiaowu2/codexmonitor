# 发布前检查清单（用户已批公开/发布；仍须完成技术与安全核验）

- [x] 用户已批三项（d428）；总指挥已确认范围。发布前仍按以下清单核验。
- [x] home 不代持、不导出、不转发密钥/助记词/配对码；gas 由用户在钱包内逐笔确认，home 不代签。
- [x] 合约地址与 ABI 已回填：`0x8d314451f99bffdac9d2d51c683a5b9aecc1ecb7`；链上只读核验 status=1、构造参数正确、`streakOf=0`、`hasCheckedInToday=false`。
- [x] BscScan 源码验证：已由用户目视确认 Source Code 页显示绿色 “Exact Match”；验证链接 https://bscscan.com/address/0x8d314451f99bffdac9d2d51c683a5b9aecc1ecb7#code 。
- [x] owner 权限披露：合约无 owner/admin/暂停/升级/特权函数。
  - tokenId 向量已核验：`keccak256(bytes("bixiaowu"))` = 首 12 位 `0x836fd94ac8c1`；完整值见 lead c32b-ack（32 字节 tokenId，不是私钥），与 lead c32b-ack 一致；合约地址到手后仍须做前端回归。
- [x] 公开仓库名、README、许可证与数据脱敏口径已确认：仓库 bixiaowu2/codexmonitor，README 保持现有对外口径，dApp 子目录密钥扫描 0 命中。
- [x] 发布渠道、文案、双标签与问卷链接已确认；禁语扫描通过；不冒充 Binance、不承诺收益、不刷量。
- [x] BscScan 源码验证：用户已目视确认 BscScan Code 页绿色 “Exact Match”（2026-11-19）；链接同上。
- [x] `python3 scripts/scan_secrets.py --root . --strict` 通过（0 命中）。
- [x] `node --check app.js` 通过。
- [x] 数据面板：`date`/`generated_at`/`sections.stocks|meme|x` 结构校验通过；静态数据不含敏感字段。
- [x] 排行榜离线正反例：localStorage 注入 fixture 的正例与畸形 fixture 的反例均通过（2026-11-19）。
- [x] 排行榜链上读取路径测试已补真实 ABI 回归（2026-11-19 夜间）：`node tests/leaderboard-chain.test.mjs` 6/6 PASS。新增正例：用 ethers 按合约真实事件编码 3 topics + 32B data，生产 ABI 能解出 `tokenId/day/streak`；新增反例：旧 ABI 解同一事件报 `data out-of-bounds`，证明旧 4/4 只测了假 parseLog、不足以覆盖生产路径。其余为有事件正例、无事件反例、查询失败反例、窗口外过滤；固定 block/log fixture，无网络/无钱包/无密钥。
- [x] 四台 Agentic 钱包首签链上核验（2026-11-19）：day 20776 各有 `CheckedIn` 事件且 status=1，4 个独立持 Agent ID 交互地址；创新奖门槛按 4/30、缺口 26 计。
- [x] `CheckedIn` ABI 与 Solidity 源码对齐（2026-11-19 夜间）：根 `app.js`、发布子目录 `app.js`、`contracts/checkin-abi.json` 均已把 `day` 标为 `indexed`；两处 app.js sha256 同为 `74f5b166833b2ca0f426055005a2db5ef271b1d0ef79f1f82a9dedc679869ffb`。
- [x] GitHub 推送与 Cloudflare 发布：ABI 修复与 6/6 回归已提交并推送公开仓库（main HEAD `b08ea30077f2db0c798701a76ef0cab8bc7614b0`）；Cloudflare Worker agent-id-checkin-dapp 已重新发布（Version deddbd17），线上 `https://agent-id-checkin-dapp.bixiaowu.workers.dev/app.js` 实测 HTTP 200、sha256 与本地修复版一致（`74f5b166833b2ca0f426055005a2db5ef271b1d0ef79f1f82a9dedc679869ffb`）；未使用邮件传 token。

当前状态（2026-11-19 夜间）：合约已部署成功（公开 TxHash：`0x90ea…af6`），前端已回填地址/ABI/只读 RPC。夜间发现并本地修复了 `CheckedIn` 事件 ABI 少标 `indexed day` 的生产 bug：原配置会让真实签到日志在排行榜解析时触发 `data out-of-bounds`；已补 ethers 真实事件编码/解码的正反例，6/6 PASS。已发布 dApp 子目录密钥扫描 12 文件 0 命中，`node --check` 与两处 app.js 哈希一致性通过。三项收尾已完成：BscScan 源码 Exact Match 已确认并回填、GitHub/Cloudflare 已发布、总指挥回执已发送；MCP 已按 dff7 撤销。**ABI 修复与 6/6 回归已推送公开仓库 main HEAD b08ea30，并已上线 Cloudflare Worker（Version deddbd17）；22:30 CST 线上 app.js 哈希实测与本地一致。** 真实签到端到端须由持 Agent ID 的用户本人签名付 gas，当前未代做。
- [x] getLogs 生产路径修复（2026-11-20 07:30 CST）：根因是公共 RPC 拒绝 `fromBlock:0→latest`；已改为部署块 `0x8047035` 起 45,000 块分片查询，无新增 key。公开只读 `https://bsc-rpc.publicnode.com` 实测读到 6 条真实 CheckedIn（day 20776），两处 app.js 同步、`node --check` 通过、6/6 离线回归 PASS。公开子目录密钥扫描 11 文件 0 命中。
