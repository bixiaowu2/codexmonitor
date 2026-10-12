"""将两个老 radar 的 forward.py 替换为 no-op 实现(保留接口签名, 调用方零改动)。
前瞻账本已无消费者, 停止记账以省 CPU 与 DB 增长。"""
import shutil

NOOP = '''"""No-op forward ledger. 记账已停用(无消费者); 接口保留以兼容调用方。"""
from __future__ import annotations


def initialize(db):
    return


def valid_number(value):
    return False


def register(db, key, instrument_key, symbol, market, entry, payload, now=None):
    return False


def normalize_marks(row, now):
    return {}


def observe(db, instrument_key, price, now=None):
    return 0


def summary(db, now=None):
    return {"ledger_version": "disabled", "tracks": 0, "open": 0, "complete": 0,
            "marks": 0, "missing_marks": 0, "horizons": {}, "grace_seconds": 0,
            "lead_time": None, "notice": "forward accounting disabled"}
'''

for p, bak in (("/opt/meme-radar/forward.py", "/opt/meme-radar/forward.py.pre-slim-20261012"),
               ("/opt/stock-radar/forward.py", "/opt/stock-radar/forward.py.pre-slim-20261012")):
    shutil.copy2(p, bak)
    open(p, "w", encoding="utf-8").write(NOOP)
    import ast
    ast.parse(open(p).read())
    print(f"{p} -> no-op (备份: {bak})")
