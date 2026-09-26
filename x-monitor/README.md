# X 账号监控：无需付费 X API

Python + Playwright 读取浏览器中可见的 X 主页帖子，通过 Telegram、钉钉及企业微信群机器人发送文字摘要和原帖链接。默认每 120 秒启动一轮检查，可按账号配置接收群。

**云端实测进展：Oracle ARM Chromium 可运行；headless 模式返回 403，但普通 Chromium 在 Xvfb 下可读取三个账号的公开帖子。默认使用后者，并适配 X 新旧两种页面结构。真实 Telegram/钉钉群收信与五分钟时效尚未验收。**

你的 Oracle Ubuntu 24.04 ARM 服务器，优先按 [ORACLE_ARM.md](ORACLE_ARM.md) 部署；已预填三个账号。通用云部署与 Telegram 配置见 [CLOUD_DEPLOY.md](CLOUD_DEPLOY.md)。

## 准备

以下命令均在仓库的 `x-monitor/` 目录执行：

```bash
cd x-monitor
```

需要 Python 3.10+、可访问 X 的网络、群机器人配置，以及 X 要求登录时可手动操作的图形环境。普通个人微信群没有此处使用的官方群机器人接口；需要先确定企业微信群或另行对接已有中转服务，目前代码支持 Telegram、钉钉和企业微信。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env  # 仅首次安装；不要覆盖已有配置
```

编辑 `.env`：填写 `X_ACCOUNTS` 和所需 webhook；Telegram 填写 `TELEGRAM_BOT_TOKEN` 与 `TELEGRAM_CHAT_ID`，未使用的 webhook 留空。URL 和密钥用单引号包住，便于 shell 安全加载。密钥只保存在本机文件，不必发到聊天里。钉钉加签填写 `DINGTALK_SECRET`；关键词安全模式可配置关键词 `X`。

按账号分群时编辑 `routes.json`，结构参考 `routes.example.json`。账号专属路由优先，其次 `default`，最后环境变量全局 webhook；空路由不会自动回退，启动检查会报错。不同机器人可以各自设置钉钉 `secret`。

## 登录、检查与运行

普通 Ubuntu 首次安装还需 `sudo apt-get install xvfb xauth`，Oracle 安装脚本已包含。当前公开主页可读取时无需先登录；如果 X 要求登录，再手动执行 `--login`。在同一终端执行：

```bash
set -a
source .env
set +a
# 只有 X 要求登录时才执行：python x_monitor.py --login
python x_monitor.py --check-config
python x_monitor.py --test-webhooks
bash scripts/run-monitor.sh --once
bash scripts/run-monitor.sh
```

`--login` 需要桌面，在浏览器里手动登录，再按回车；程序检查登录界面标志并保存本机浏览器 profile，不索取密码。不绕过验证码或访问控制。跨主机复制 Chromium profile 可能因系统加密或浏览器版本而失效，应在最终运行环境核实登录。

`--check-config` 只做静态检查，不证明登录有效、URL 可达或群能收到消息。`--test-webhooks` 会向全部已配置目标实际发送测试消息；没有目标或未收到合法成功响应会失败。`--once` 执行一轮，抓取失败或仍有待发消息时返回非零退出码。

## 状态与可靠性

- 每个账号首次成功抓取独立建立历史基线，不发送已有帖子；其他账号失败不会阻断已初始化账号。
- 新帖先写入 `data/state.json` 待发队列再推送，按目标分别保存成功确认。未确认目标在运行循环中每约 30 秒重新尝试，页面抓取占用时间会延后重试。
- 重启后继续发送待发帖子，即使帖子已经不在当前主页。删除状态文件会丢失待发消息。状态损坏会报错，不会静默清空。
- webhook 没有事务或幂等键，若群已收到但响应丢失，或成功响应之后落盘之前进程崩溃，仍可能重复。并非严格恰好一次投递。
- 每轮最多读取 `MAX_ITEMS_PER_ACCOUNT` 张卡片，最多滚动五次。突发大量发帖、X 未展示或延迟展示内容、断网和登录失效都可能造成漏检或延迟。长帖摘要会截断，完整内容看原帖链接。
- 用帖子 ID 高水位避免旧置顶帖重复通知；这也意味着旧帖的新转发动作不能可靠识别。当前监控主页可见的新帖子，不保证覆盖回复标签页、所有转发动作或全文/媒体附件。

## 五分钟目标如何验收

120 秒是轮询配置，不是送达保证。多账号串行加载、X 限流、网络或 webhook 故障均可超过五分钟。日志记录机器人确认时的帖子年龄，超过 300 秒会警告；机器人确认也不等于每个成员已经看到消息。

配置真实账号和群后：先建立基线，再等待目标账号真实发帖，对照原帖时间、程序发现/确认日志及两个群的可见接收时间；逐条确认小于 300 秒，并模拟重启、一个群暂时拒收和登录失效。未完成这一步前不能声称达到用户的时效目标。

## 部署

单实例运行，不能让多个进程共用 profile 和状态文件。优先在完成登录的常开电脑上运行。`x-monitor.service.example` 是需要修改路径及运行用户的 systemd 模板；此服务器默认 `HEADLESS=false`，通过 `scripts/run-monitor.sh` 启动 Xvfb 虚拟显示器；没有桌面也可后台运行。

Docker Compose 提供部署模板，镜像和 Python Playwright 固定为同一版本。先配置 `.env`、`routes.json`，并确认最终环境的登录会话可用：

```bash
docker compose build
docker compose run --rm x-monitor python x_monitor.py --check-config
docker compose run --rm x-monitor python x_monitor.py --test-webhooks
docker compose up -d
docker compose logs -f x-monitor
```

容器默认普通 Chromium + Xvfb 虚拟显示，挂载浏览器 profile 和状态目录；Xvfb 不提供可交互远程桌面，不能用它代替手动登录界面。不要将本机密钥、实际路由或 profile 打进镜像、压缩包或版本库。

## 本地验证

```bash
python -m unittest discover -s tests -v
```

测试涵盖独立基线、旧帖过滤、失败重试及重启恢复、机器人业务拒收、错误响应和测试命令目标覆盖。它们不访问真实 X，不向真实群发消息。


## 新增 KOL / 生态账户与 Meme 共享出口

新增账户统一维护在 `x_accounts.json`。20个初始条目包含9个新增研究、风险调查和官方生态账户，以及原有3个Binance账户，并补充跨链链上数据和高影响力观点账户。账户身份参考公开来源，尚未以喊单收益回测验证；官方账号与风险调查账号不等于推荐买入。

配置 `X_EXTRA_ACCOUNTS_FILE=./x_accounts.json` 后，程序每轮重新读取清单；新增账户的帖子通过原有Telegram/钉钉配置和持久化队列投递，逐渠道确认、失败重试。`routes.json` 如有单独账户规则则优先使用该规则。首次成功读取只建立基线，不补发历史帖。原账户仍由 `X_ACCOUNTS` 优先轮询，同名条目不重复采集。

新增账户目标间隔120秒，每轮最多6个；额外采集预算60秒，接近原轮次截止则停止，按最早到期轮转。正常情况下每个新增账户约2–4分钟检查一次，页面失败会退避至最多1小时；因此不是X发帖实时订阅，也不能保证任何情况下5分钟内送达。每次可见最多8条，极高频账户可能超出单轮可见范围。

`X_PUBLIC_FEED_DB` 指定只含公开原帖的SQLite出口，记录24小时内的帖子与账户采集健康，不写入Token、Webhook或浏览器会话。原帖作者须与监控账户一致；转发别人的卡片不冒充作者本人。共享出口故障不会取消已入队的新帖通知。

云端跨服务配置示例（需要已存在meme-radar用户组）：

```bash
sudo install -d -o ubuntu -g meme-radar -m 2750 /var/lib/meme-x-feed
# X服务环境中：X_PUBLIC_FEED_DB=/var/lib/meme-x-feed/feed.sqlite
# Meme服务环境中：MEME_X_PUBLIC_FEED_DB=/var/lib/meme-x-feed/feed.sqlite
```

数据库由X服务用户创建，0640权限；Meme服务只读。部署后要分别核验账户成功采集时间、通知投递结果和Meme的 `latest.social` 字段，不能只凭进程active判断接通。
