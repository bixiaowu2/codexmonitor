# Meme Radar v0.2

六链公开 DEX 池候选雷达：BSC、Solana、Robinhood Chain、X Layer、Arc、Stable。仅研究与提醒，无交易或私钥功能。当前属于数据采集与排名基础版，未达到完整 GMGN 功能。

## 真实覆盖

使用 GeckoTerminal `/networks/{network}/new_pools` 与 `trending_pools`，每端点最多20池。网络 ID：`bsc`、`solana`、`robinhood`、`x-layer`、`arc`、`stable`。四条新增链的真实响应已保存为测试样本。先前关于 Arc/Stable 无池端点的描述不正确。

每60秒计划发起一轮；请求间隔至少3.2秒，单次请求按配置超时，429/失败按端点退避60–900秒。每轮240秒硬截止。执行时间超过60秒则顺延，聚合器也可能延迟，所以不是秒级发现、更不是全链穷举。故障与空结果分别记录；退避时不把旧池作为新候选。BSC/Solana/Robinhood同时接入DexScreener最新资料/推广列表作为独立备用发现源：每轮共享两次列表请求，先按链过滤后最多查询5个合约。推广不代表质量，亦不加分；备用池不会覆盖GeckoTerminal同池观测。

## 排名与风险

保留5分钟/1小时/24小时成交、变动和交易笔数，缺失为未知。Solana地址保留大小写。每代币选储备最多的池评分，避免重复占榜。基础门槛为池储备5000美元与1小时量1000美元；按池年龄、成交笔数买入占比、成交量/储备和动能排序，5分钟急涨降分。

排名是未经回测校准的启发式；不等于百倍概率。候选来自DEX池，不保证都是Meme；稳定币、包装资产、股票映射资产可能进入样本。池储备不等于实际可卖出深度，成交笔数不等于真实独立买家或净流入。合约权限/蜜罐、持仓集中度、关联钱包、解锁尚未自动验证。

可选钱包与KOL/X输入文件必须包含链、精确合约、24小时内时间及HTTPS来源；同名符号不能匹配。输入只是外部证据，暂不加分。尚未配置自动聪明钱包跟踪、X/KOL抓取或操盘机构识别。

## 通知与存储

默认 Telegram / 钉钉均关闭，独立于 Alpha 和 X 服务。关闭时只保存样本，不积压通知。启用后每小时发送当前前5名排名，各渠道独立重试，15分钟过期，历史版本队列失效。发送超时仍存在远端已接收但本地未确认导致重试重复的可能。无秒级告警、自动买卖或持仓止盈止损功能。

SQLite保存 `runtime.latest`、`runtime.health`、最新池快照7天、每15分钟候选快照90天、通知记录7天。候选快照是后续评估基础，尚不包含完整收益追踪/动态模型优化。日志有每链覆盖数与接口失败状态。

## 部署

Python3.10+标准库，无Docker或模型API调用。Oracle ARM64 Ubuntu24.04用systemd运行。复制 `.env.example` 为 `/etc/meme-radar.env`（0600）；运行 `sudo bash deploy/install-systemd.sh`，然后 `sudo systemctl enable --now meme-radar`。升级前备份代码、环境文件、unit及SQLite；安装脚本本身不会替你备份。

测试：`python3 -m unittest -v`。`python3 radar.py`会执行一轮真实采集，使用临时 `MEME_DATA` 可避免影响线上数据库。生产查看 `journalctl -u meme-radar`；详细状态从SQLite的runtime读取。主循环退出自动重启，CPU限额1核，内存限额768MiB。
