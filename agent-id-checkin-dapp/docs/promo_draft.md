# 黑客松推广文案口径（公开仓库存档）

> 本文件只存对外可核查的项目事实与文案边界。实际发帖由用户本人完成，完整最终稿以桌面定稿 `~/桌面/agent-id-x-promotion-drafts.md` 版本 D 为唯一来源，本文件不复制、不改写帖子正文。

## 已核验事实

- dApp：https://agent-id-checkin-dapp.bixiaowu.workers.dev （Cloudflare Version `deddbd17`；线上 `/app.js` 与本地哈希一致）
- 合约：`0x8d314451f99bffdac9d2d51c683a5b9aecc1ecb7`（BscScan 源码页“Exact Match”已由用户目视确认）
- 公开仓库：https://github.com/bixiaowu2/codexmonitor/tree/main/agent-id-checkin-dapp （main HEAD `ea5627c97b161a844ea06c702b62c106924c8386`）
- 排行榜读取真实事件 ABI：`CheckedIn(uint256 indexed tokenId, uint256 indexed day, uint256 streak)`；`node tests/leaderboard-chain.test.mjs` 6/6 PASS。
- 链上交互地址：4/30，缺口 26 由 lead 协调社区渠道；home 不刷量、不批量注册、不碰资金或白名单。
- 合约无 owner、无升级、无提款、不托管资产；钱包签名与 gas 由用户本人逐笔确认。

## 对外渠道短文（仅作事实底稿）

Agent ID 每日签到 dApp 已上线：连接浏览器钱包 → 用持有 Agent ID 签到 → 查看当日雷达数据；连续天数上链，排行榜只读 CheckedIn 事件。无 owner、无升级、无提款、不托管资产，不承诺收益、非投资建议。

## 禁止项

- 不冒充 Binance 品牌，不承诺收益或被动收入；
- 不刷量、不批量注册、不收集助记词/私钥/API key/配对码；
- 不声称用户未确认的资格、奖项或数据效果；
- 不把本地草稿当最终发帖正文；最终帖只用桌面定稿版本 D，不改字。
