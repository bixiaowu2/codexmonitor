#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$project_dir"
if [[ "$EUID" -eq 0 ]]; then
  echo 'Run as your normal SSH user (for example ubuntu), not root. sudo is used only for system packages.' >&2
  exit 2
fi
if [[ ! -f /etc/os-release ]]; then echo 'Ubuntu 24.04 required' >&2; exit 2; fi
source /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 24.04 ]]; then echo 'This installer targets Ubuntu 24.04.' >&2; exit 2; fi
case "$(uname -m)" in
  aarch64|x86_64) ;;
  *) echo 'Unsupported architecture' >&2; exit 2 ;;
esac
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip xauth xvfb
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# Selects the browser build for this server architecture.
.venv/bin/python -m playwright install --with-deps chromium
if [[ ! -f .env ]]; then cp .env.example .env; fi
if [[ ! -f routes.json ]]; then printf '{}\n' > routes.json; fi
chmod 600 .env routes.json
mkdir -p data browser-profile
chmod 700 data browser-profile
.venv/bin/python - <<'PY'
import platform
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content('<title>ARM runtime check</title><p>ok</p>')
    assert page.title() == 'ARM runtime check'
    b.close()
print('Chromium local smoke test passed on', platform.machine())
PY
printf '\nRuntime ready. Configure .env, complete X login, test delivery, then install the service.\n'
