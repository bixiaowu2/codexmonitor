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
- [x] 排行榜链上读取路径可注入测试（2026-11-19）：`node tests/leaderboard-chain.test.mjs` 4/4 PASS（有事件正例、无事件反例、查询失败反例、窗口外过滤）；固定 block/log fixture，无网络/无钱包/无密钥。真实合约地址到手后再用同一路径跑一次端到端。
- [x] GitHub 推送与 Cloudflare 发布：main HEAD acf53eb，Cloudflare Worker agent-id-checkin-dapp 已发布（Version b8d14f6d）；未使用邮件传 token。

当前状态（2026-11-19）：合约已部署成功（公开 TxHash：`0x90ea53f9cc379fc749b26b19e5335e62a865f32b64274f8748af842040241af6`），前端已回填地址/ABI/只读 RPC；已发布 dApp 子目录密钥扫描 11 文件 0 命中，`node --check` 与排行榜链上路径 4/4 离线测试通过。三项收尾已完成：BscScan 源码 Exact Match 已确认并回填、GitHub/Cloudflare 已发布、总指挥回执已发送；MCP 已按 dff7 撤销。真实签到端到端须由持 Agent ID 的用户本人签名付 gas，当前未代做。
