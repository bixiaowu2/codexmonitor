# GMGN 只读增强

Meme 雷达可以选用 GMGN 官方只读 API 补充实时价格、合约安全、持仓结构和钱包相关数据。它是增强层，不替换 GeckoTerminal/DexScreener，也不改变安全检查的阻断规则。

## 配置

API Key 只放在服务器的 `/etc/gmgn-readonly.env`，权限为 `0600`：

```dotenv
GMGN_API_KEY=你的个人APIKey
```

不要配置 `GMGN_PRIVATE_KEY`，不要把 API Key、签名私钥或钱包私钥放入 Git、日志、Telegram 或钉钉。服务只调用查询接口，不安装或调用 swap/order。

## 覆盖边界

当前每轮最多增强 `GMGN_MAX_CHECKS` 个流动性最高的候选，默认 1 个；每个候选查询 token info 和 token security。Key 缺失、额度耗尽、IPv4/网络失败或 HTTP 429 时会退避，并保留原有公开 DEX 数据；消息会显示 GMGN 状态和增强数量。GMGN 返回的字段保存在候选的 `gmgn` 节点，不直接增加买入评分。

官方文档列出的 API 链包括 SOL、BSC、Base 等，具体新链覆盖以账号实际响应为准；Arc、Robinhood、Stable、X Layer 不会因为命令参数被假定为已覆盖。
