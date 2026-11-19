#!/usr/bin/env python3
"""Minimal, opt-in promotion of the checked-in static dApp into codexmonitor.

Boundary: this only copies the release-not-blocking sources into the existing
public radar backup repo, on the explicit d428 approval. It does not create a
new remote repo, does not deploy, does not sign transactions, and never reads
credentials. Run from the project root after `scripts/scan_secrets.py --strict`.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = Path("/srv/codexmonitor/agent-id-checkin-dapp")
FILES = ["README.md", "RELEASE_CHECKLIST.md", "index.html", "app.js", "styles.css",
         "config.example.json", "contracts/checkin-abi.json", "data/latest.json",
         "docs/promo_draft.md", "scripts/scan_secrets.py", "tools/sync_radar_export.py"]

def main():
    if not DEST.parent.is_dir():
        raise SystemExit(f"缺本地检出目录: {DEST.parent}")
    DEST.mkdir(parents=True, exist_ok=True)
    for rel in FILES:
        src = ROOT / rel
        if not src.is_file():
            raise SystemExit(f"缺源文件: {src}")
        out = DEST / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        print("copied", rel)

if __name__ == "__main__":
    main()
