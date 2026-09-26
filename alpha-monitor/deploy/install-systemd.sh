#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo '请使用 sudo bash deploy/install-systemd.sh' >&2
  exit 1
fi
src=$(cd -- "$(dirname -- "$0")/.." && pwd)
command -v python3 >/dev/null || { echo '请安装 python3' >&2; exit 1; }
command -v curl >/dev/null || { echo '请安装 curl ca-certificates' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
if ! id alpha-radar >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/alpha-radar --no-create-home --shell /usr/sbin/nologin alpha-radar
fi
# Stop existing version before replacing its files. Retain database and credentials.
if systemctl is-active --quiet alpha-radar; then systemctl stop alpha-radar; fi
install -d -m 0755 /opt/alpha-radar /opt/alpha-radar/data
install -d -m 0700 -o alpha-radar -g alpha-radar /var/lib/alpha-radar
install -m 0644 "$src/radar.py" "$src/cloud.py" "$src/healthcheck.py" "$src/cycle.py" "$src/strategy.py" "$src/positions.py" "$src/telegram_commands.py" "$src/safety.py" "$src/dingtalk.py" "$src/alerts.py" "$src/fast_lane.py" "$src/evaluation.py" "$src/ranking.py" /opt/alpha-radar/
install -m 0644 "$src/data/research.json" /opt/alpha-radar/data/research.json
install -m 0644 "$src/deploy/alpha-radar.service" /etc/systemd/system/alpha-radar.service
if [ ! -e /etc/alpha-radar.env ]; then
  install -m 0600 "$src/.env.example" /etc/alpha-radar.env
fi
systemctl daemon-reload
printf '%s\n' '安装完成。填写 /etc/alpha-radar.env，测试Telegram后运行：sudo systemctl enable --now alpha-radar' '更新安装会暂停旧服务，完成配置/检查后请重新启动；数据库和凭据已保留。'
