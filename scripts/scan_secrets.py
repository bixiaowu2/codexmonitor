#!/usr/bin/env python3
"""本地发布前密钥扫描：只扫指定目录，只报文件名/行号/规则名，不输出命中值。

用法：
  python3 scripts/scan_secrets.py --root .
  python3 scripts/scan_secrets.py --root . --strict   # 有命中则退出 1

退出码：0=无命中，1=有命中（--strict），2=参数/目录错误。
"""
import argparse
import os
import re
import sys

PATTERNS = [
    ("ETH_PRIVATE_KEY", re.compile(r"\b0x[0-9a-fA-F]{64}\b")),
    ("PEM_PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GENERIC_API_TOKEN", re.compile(r"\b(?:sk|ghp|gho|ghs|ghu|github_pat|xoxb|xoxp)_[A-Za-z0-9_\-]{20,}\b")),
    ("MNEMONIC_12_24", re.compile(r"\b(?:[a-z]{3,}\s+){11,23}[a-z]{3,}\b")),
    ("HARDCODED_SECRET_PATH", re.compile(r"(?:ssh-key|\.credentials|\.env\b|id_rsa|id_ed25519)", re.I)),
    ("HARDCODED_KEY_PATH_CONST", re.compile(r"^\s*(?:SSH_KEY|PRIVATE_KEY|KEY_PATH|KEY_FILE)\s*=", re.I | re.M)),
]

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".mp4", ".mov", ".zip", ".gz", ".ico")
SKIP_RELATIVE_PATHS = {"scripts/scan_secrets.py"}
MAX_BYTES = 2 * 1024 * 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="只扫描这个目录，不递归到凭据目录")
    ap.add_argument("--strict", action="store_true", help="发现命中时退出 1")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"目录不存在: {root}", file=sys.stderr)
        return 2

    hits = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if name.endswith(SKIP_SUFFIXES) or rel in SKIP_RELATIVE_PATHS:
                continue
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    lines = content.splitlines()
            except OSError:
                continue
            scanned += 1
            for lineno, line in enumerate(lines, 1):
                for rule, pattern in PATTERNS:
                    if pattern.search(line):
                        hits.append((os.path.relpath(path, root), lineno, rule))

    for path, lineno, rule in hits:
        print(f"HIT\t{rule}\t{path}:{lineno}")
    print(f"SCANNED_FILES={scanned}\tHITS={len(hits)}")
    if hits and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
