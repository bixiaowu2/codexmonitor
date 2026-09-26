#!/usr/bin/env bash
set -euo pipefail
src=$(cd "$(dirname "$0")/.." && pwd)
cd "$src"
python3 -m unittest -v
python3 - <<'PY'
from pathlib import Path
for line in Path('/etc/meme-radar.env').read_text().splitlines():
    if line.split('=',1)[0] in ('MEME_TELEGRAM_ENABLED','MEME_DINGTALK_ENABLED'):
        assert line.split('=',1)[1].strip().lower()=='false', 'notification state requires inspection'
PY
backup=/var/backups/meme-radar/$(date -u +%Y%m%dT%H%M%SZ)-v02
install -d -m 0700 "$backup"
cp -a /opt/meme-radar "$backup/code"
cp -a /etc/meme-radar.env "$backup/env"
cp -a /etc/systemd/system/meme-radar.service "$backup/unit"
systemctl stop meme-radar
rollback() {
    cp -a "$backup/code/." /opt/meme-radar/
    cp -a "$backup/env" /etc/meme-radar.env
    cp -a "$backup/unit" /etc/systemd/system/meme-radar.service
    systemctl daemon-reload
    systemctl start meme-radar
    echo 'UPGRADE_FAILED_ROLLED_BACK'
}
trap rollback ERR
python3 - "$backup" <<'PY'
import sqlite3,sys
with sqlite3.connect('/var/lib/meme-radar/meme-radar.sqlite') as src:
    with sqlite3.connect(sys.argv[1]+'/meme-radar.sqlite') as dst:src.backup(dst)
PY
bash deploy/install-systemd.sh
install -m 0644 README.md /opt/meme-radar/README.md
python3 - <<'PY'
from pathlib import Path
p=Path('/etc/meme-radar.env');lines=p.read_text().splitlines();key='MEME_CHAINS='
lines=[x for x in lines if not x.startswith(key)]
lines.append(key+'bsc,solana,robinhood,xlayer,arc,stable')
p.write_text('\n'.join(lines)+'\n');p.chmod(0o600)
PY
systemctl enable meme-radar >/dev/null
systemctl restart meme-radar
systemctl is-active --quiet meme-radar
trap - ERR
echo "UPGRADE_OK backup=$backup"
systemctl is-active meme-radar alpha-radar x-monitor
