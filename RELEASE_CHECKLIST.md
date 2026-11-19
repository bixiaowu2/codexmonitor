# 发布前检查清单（用户已批公开/发布；仍须完成技术与安全核验）

- [x] 用户已批三项（d428）；总指挥已确认范围。发布前仍按以下清单核验。
- [x] home 不代持、不导出、不转发密钥/助记词/配对码；gas 由用户在钱包内逐笔确认，home 不代签。
- [ ] 合约地址、ABI、BscScan 验证链接与 owner 权限披露（无 owner/admin/暂停/升级/特权函数）已回填。
  - tokenId 向量已核验：`keccak256(bytes("bixiaowu"))` = 首 12 位 `0x836fd94ac8c1`；完整值见 lead c32b-ack（32 字节 tokenId，不是私钥），与 lead c32b-ack 一致；合约地址到手后仍须做前端回归。
- [ ] 公开仓库名、README、许可证与数据脱敏口径已确认
- [ ] 发布渠道、文案、双标签与问卷链接已确认；禁语扫描通过；不冒充 Binance、不承诺收益、不刷量。
- [x] `python3 scripts/scan_secrets.py --root . --strict` 通过（0 命中）。
- [x] `node --check app.js` 通过。
- [x] 数据面板：`date`/`generated_at`/`sections.stocks|meme|x` 结构校验通过；静态数据不含敏感字段。
- [x] 排行榜离线正反例：localStorage 注入 fixture 的正例与畸形 fixture 的反例均通过（2026-11-19）。
- [ ] 排行榜链上正反例：合约地址 + 只读 RPC 就绪后，跑有事件、无事件、查询失败三类并确认只读取链上事件。
- [ ] GitHub 推送与 Pages 发布：需合法认证/已授权空仓库；不得通过邮件传 token。

当前状态（2026-11-19 上午）：已 git init 并提交本地 main；未推送、未部署、未发布、未花 gas。公开仓库与发布认证路径待 lead 提供。
