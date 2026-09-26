# codexmonitor

监控程序集合。每个程序使用独立目录管理源码、配置模板、测试和部署文件；真实 Token、Webhook、浏览器会话、运行数据库和私钥不进入仓库。

| 目录 | 程序 | 说明 |
| --- | --- | --- |
| [x-monitor](x-monitor/) | X 账户监控 | 采集公开 X 帖子并推送到 Telegram / 钉钉 |
| [alpha-monitor](alpha-monitor/) | 币安 Alpha 监控 | Alpha 上市、合约交集、BSC 历史研究和候选排序 |
| [meme-monitor](meme-monitor/) | 六链 Meme 雷达 | BSC、Solana、Robinhood、X Layer、Arc、Stable 池监控和定期排名 |
| `stock-monitor/` | 股票监控 | 后续开发 |

三个现有程序在云服务器上分别由独立 systemd 服务管理，配置和状态目录互相隔离。仓库内的 `.env.example` 仅是模板；部署时在服务器创建实际 `.env` 或 `/etc/*-radar.env`。

## 本地测试

```bash
cd x-monitor && python3 -m unittest discover -s tests -v
cd ../alpha-monitor && python3 -m unittest discover -v
cd ../meme-monitor && python3 -m unittest -v
```

## 安全边界

仓库只保存公开源码和脱敏模板，不保存 Telegram Bot Token、钉钉 Webhook/Secret、X 浏览器 profile、SSH 私钥、SQLite 运行库或服务器日志。任何候选排序都不是收益保证，也不执行自动交易。
