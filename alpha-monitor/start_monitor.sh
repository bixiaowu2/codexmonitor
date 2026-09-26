#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
command -v python3 >/dev/null
command -v curl >/dev/null
exec python3 radar.py watch --chain 56 --data live --interval 300 --enrich 12 "$@"
