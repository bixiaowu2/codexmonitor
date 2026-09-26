# 云服务器常驻：X → Telegram + 钉钉

从 GitHub 克隆本仓库后，先进入 `x-monitor/` 子目录；下文命令均在该目录执行。

建议从一台 Ubuntu 24.04、2 核 / 4GB 内存的服务器和少量监控账号开始。服务器需能访问 X、api.telegram.org 和钉钉机器人地址。无需 X 付费 API；Telegram 使用 Telegram Bot API，这是消息发送接口，不是 X API。

Oracle ARM 服务器已能使用普通 Chromium + Xvfb 读取三个账号的公开帖子；headless 浏览器在同一服务器返回 403，因此默认禁用 headless。真实群推送和五分钟送达仍需配置机器人后测量。

## 1. 配置消息目标

Telegram：使用官方 @BotFather 创建机器人，将机器人加入目标群并允许发消息；若目标是频道，需授予发帖权限。填写 Bot Token 和目标 `chat_id`（群通常是负数；公开频道可用 `@channelname`）。私聊需用户先主动向机器人发送消息。不要把 Token 交给陌生的群 ID 查询网站。

钉钉：在目标群添加自定义机器人，取得 webhook；采用加签时填 Secret。若采用关键词安全策略，可配置关键词 `X`。

在服务器项目目录只在首次部署创建 `.env`，配置以下字段：

```env
X_ACCOUNTS='binancezh,cz_binance,heyibinance'
POLL_SECONDS=120
HEADLESS=false
TELEGRAM_BOT_TOKEN='填写BotFather提供的Token'
TELEGRAM_CHAT_ID='填写目标群或频道ID'
DINGTALK_WEBHOOK='填写群机器人webhook'
DINGTALK_SECRET='填写加签Secret，未启用则留空'
WECOM_WEBHOOK=
BROWSER_PROFILE_DIR=./browser-profile
STATE_FILE=./data/state.json
ROUTES_FILE=./routes.json
```

默认 `routes.json` 为 `{}`：全部账号同时推送到 Telegram 和钉钉。如需分群，参考 `routes.telegram.example.json`，只填实际需要的路由。密钥文件留在服务器，不用发到聊天里。

```bash
cp .env.example .env  # 仅首次执行，然后编辑；不能覆盖已有密钥
chmod 600 .env routes.json
```

## 2. 在最终运行环境完成首次登录

当前公开账号已可读取，可以先跳过登录；仅在 X 要求登录时执行本节。优先在该服务器上通过受保护的远程桌面或 SSH X11 转发打开浏览器，执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
set -a
source .env
set +a
python x_monitor.py --login
```

`--login` 强制使用有界面模式，需要可用的显示服务；在普通无桌面 SSH 终端直接执行会失败。完成登录后回终端按回车，程序检查登录标志，再关闭浏览器保存 profile。不要同时运行登录浏览器和监控程序。

跨机器直接复制 Chromium profile 可能失效，甚至同机切换宿主机/容器也可能受运行用户、密钥环和浏览器环境影响。因此复制 profile 不是已验证的登录迁移方案。首次 `--once` 必须实际读到目标账号帖子；如果宿主机登录可用而容器会话不可用，可在同一宿主机用 systemd 常驻，或在容器内配置受保护的显示服务完成登录。

## 3. 启动方式二选一

### Docker Compose

需要已安装并启用 Docker Engine 与 Compose 插件。项目无需对公网开放监听端口。

```bash
docker compose build
docker compose run --rm x-monitor python x_monitor.py --check-config
docker compose run --rm x-monitor python x_monitor.py --test-webhooks
docker compose run --rm x-monitor python x_monitor.py --once
docker compose up -d
docker compose ps
docker compose logs --tail=100 -f x-monitor
```

容器设置 `HEADLESS=false` 并通过 Xvfb 启动普通 Chromium，不需要物理显示器。配置包含：

- `restart: unless-stopped`：进程退出后重启；服务器重启后须由系统启动 Docker，且该容器未被手动停止。
- 浏览器 profile、状态及待发队列持久化挂载。
- 日志轮转，最多约 3 × 10MB。
- `--health`：最近五分钟各账号抓取成功且没有超龄待发队列才返回健康。

`unhealthy` 只标记状态，Docker Compose 不会因此自动重启容器。登录失效也不能靠反复重启修好。基础镜像构建此前在当前机器超时，不能视为已通过镜像验收。

### 同一登录环境使用 systemd

把项目放到 `/opt/x-monitor`（或修改模板里的全部路径），编辑 `x-monitor.service.example` 的 `User` 为拥有 profile 和项目文件的实际 Linux 用户。该用户须能写入 `browser-profile` 和 `data`。后台模式在 `.env` 里设 `HEADLESS=false`。

```bash
sudo cp x-monitor.service.example /etc/systemd/system/x-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now x-monitor
sudo systemctl status x-monitor
sudo journalctl -u x-monitor -f
```

不要同时启用 Compose 和 systemd 监控同一个 profile / 状态文件。

## 4. 无人值守的边界和验收

正常登录态和网络可用时无需持续人工操作。遇到短暂消息发送失败，待发队列保留并重试；一个群失败不阻断另一个群，已确认的目标不主动重发。响应丢失或确认后未落盘就崩溃仍可能产生重复消息。

连续三次抓取失败会尝试向配置目标发送异常通知，每 30 分钟最多重发一次；恢复后发恢复通知。如果消息通道本身也不可达、服务器完全离线，则程序无法自行把告警送出，需靠云平台监控或独立探活服务。当前没有部署外部探活。

登录过期、验证码、账号访问受限或网页结构变化可能需要人工处理。重新登录前先停止监控，再运行 `--login`，然后恢复服务。不要删除状态目录来修复登录。

真实验收：确认启动时历史帖子不刷屏；等待目标账号发新帖，检查 Telegram 和钉钉的真实接收时间与原帖时间是否小于 300 秒；随后测试重启、一个群暂时拒收和浏览器会话失效。程序每 120 秒启动检查不等于始终五分钟送达。
