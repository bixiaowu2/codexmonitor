#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$project_dir"
if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="$PYTHON_BIN"
elif [[ -x "$project_dir/.venv/bin/python" ]]; then
  python_bin="$project_dir/.venv/bin/python"
else
  python_bin="python3"
fi
# EnvironmentFile (systemd), env_file (Compose), or the caller loads config.
case "${HEADLESS:-false}" in
  true|1|yes|on)
    export HEADLESS=true
    exec "$python_bin" x_monitor.py "$@"
    ;;
  *)
    export HEADLESS=false
    command -v xvfb-run >/dev/null || { echo 'Install xvfb and xauth first.' >&2; exit 2; }
    exec xvfb-run -a -s '-screen 0 1440x1000x24 -nolisten tcp' "$python_bin" x_monitor.py "$@"
    ;;
esac
