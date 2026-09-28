# Oracle Ubuntu 24.04 ARM 部署

从 GitHub 克隆本仓库后，先进入 `x-monitor/` 子目录；下文命令均在该目录执行。

目标环境：Ubuntu 24.04.4 LTS，6.17.0-1011-oracle，aarch64。

预设监控账号：`binancezh`、`cz_binance`、`heyibinance`；X 上可访问的 CZ 账号是 `cz_binance`（`czbinance` 返回 404）。额外账号清单默认关闭。运行方式为普通 Chromium + Xvfb。默认同时推送 Telegram 和钉钉，企业微信可不配置。

## 推荐：原生 Python + systemd

这样首次登录和常驻监控使用相同用户、机器和浏览器环境。请用普通 SSH 用户（Oracle 常用 `ubuntu`），不要用 root 登录 X。以下命令在云服务器执行，不是在本地电脑执行。

上传交付压缩包，解压并进入其中的 `x-monitor-no-api` 文件夹后：

```bash
bash scripts/install-ubuntu.sh
nano .env
```

安装脚本需要 sudo，安装 Python venv、xauth、Chromium 所需系统库及当前架构的 Playwright 浏览器；最后在本机页面执行无网络浏览器启动测试。只有在你的 ARM 服务器执行成功，才算 ARM 运行时验证完成。默认账号已填写，只需编辑消息目标：

```env
X_ACCOUNTS=binancezh,cz_binance,heyibinance
HEADLESS=false
TELEGRAM_BOT_TOKEN='从BotFather获得的Token'
TELEGRAM_CHAT_ID='目标群或频道ID'
DINGTALK_WEBHOOK='钉钉群机器人地址'
DINGTALK_SECRET='启用加签时填写，否则留空'
```

### 检查及按需登录

当前三个公开主页可直接读取，不需要先登录。只有 X 要求登录时才需执行 `--login`，它需要可交互图形会话；Xvfb 只是后台虚拟显示。可用受保护的远程桌面；或本地安装 X server 后通过 SSH X11 转发（服务器也需允许 X11Forwarding）。不要将桌面端口直接暴露到公网。普通无图形 SSH 中 `--login` 无法弹出窗口。

```bash
# 在带显示服务的 SSH/远程桌面终端，进入项目目录后执行
set -a
source .env
set +a
# 如 X 要求登录，手动执行：.venv/bin/python x_monitor.py --login
.venv/bin/python x_monitor.py --check-config
.venv/bin/python x_monitor.py --test-webhooks
bash scripts/run-monitor.sh --once
```

确认 X 页面读到帖子，以及 Telegram 和钉钉都收到测试消息。`--once` 第一次只建立基线，不发送历史帖子。

### 开机自启

```bash
bash scripts/install-systemd.sh
sudo systemctl start x-monitor
sudo systemctl status x-monitor
sudo journalctl -u x-monitor -f
```

脚本按当前用户和实际项目路径生成服务文件，启用开机启动。进程退出后每 30 秒尝试重启。服务安装脚本不主动发送消息，常驻服务需最后的 `systemctl start` 启动。服务调用 Xvfb 包装脚本运行普通 Chromium，避免本次实测的 headless 403。项目目录不要使用空格或特殊字符，不要同时启动多个监控实例。

检查健康状态：

```bash
set -a; source .env; set +a
.venv/bin/python x_monitor.py --health
# 退出码 0：近期成功抓取且无超龄待发消息；1：需要检查
```

重新登录：先 `sudo systemctl stop x-monitor`，在有显示的会话运行 `--login`，然后 `sudo systemctl start x-monitor`。保留 `data/state.json`，避免丢失待发消息和去重记录。

## Docker 备选

已查询 `mcr.microsoft.com/playwright/python:v1.55.0-noble` 镜像清单，包含 `linux/arm64` 和 `linux/amd64`。原生 ARM Docker 会自动选择 ARM 镜像；不要指定 `--platform linux/amd64`。

Python Playwright 固定为 `1.55.0`，与基础镜像匹配。Docker 镜像尚未在此云服务器上实际构建、启动，镜像清单支持不等于完整端到端验证；原生 Python 浏览器路径已实际测试。宿主机浏览器 profile 不保证能直接在容器复用，故优先推荐上述原生方案。

## 常驻与时效

默认 120 秒轮询、独立账号基线、按群确认、持久待发队列和失败重试。连续三次抓取失败时尝试发送告警，恢复时尝试发送恢复通知。登录过期、验证码或 X 页面改变仍可能需要人工干预。

Oracle 服务器的具体出口能否读取 X、发 Telegram 和钉钉，必须从服务器实测。服务恢复/消息重试可以自动处理常见短暂故障，无法保证永久无人介入或固定五分钟送达。真实验收应对比原帖时间和两个目标群可见接收时间。

更完整的机器人配置及异常处理说明见 `CLOUD_DEPLOY.md`。
