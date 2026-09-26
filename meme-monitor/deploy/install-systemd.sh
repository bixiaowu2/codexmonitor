#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'run as root'; exit 1; }
src=$(cd "$(dirname "$0")/.." && pwd)
id meme-radar >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/meme-radar --no-create-home --shell /usr/sbin/nologin meme-radar
systemctl stop meme-radar 2>/dev/null || true
install -d -m 0755 /opt/meme-radar
install -d -m 0700 -o meme-radar -g meme-radar /var/lib/meme-radar
install -m 0644 "$src"/*.py /opt/meme-radar/
install -d -m 0755 /opt/meme-radar/fixtures
install -m 0644 "$src"/fixtures/*.json /opt/meme-radar/fixtures/
install -m 0644 "$src/deploy/meme-radar.service" /etc/systemd/system/meme-radar.service
[ -e /etc/meme-radar.env ] || install -m 0600 "$src/.env.example" /etc/meme-radar.env
systemctl daemon-reload
