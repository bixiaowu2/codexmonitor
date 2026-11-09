#!/usr/bin/env python3
"""spacing 采样（路线 A：systemd timer 直跑，不经 Claude 会话）。

窗口 C-2 一次性采样：12 轮、每轮 GMGN 3 次、轮内 spacing 5.0s、单轮墙钟 ≥15s。
只读 GMGN 排行榜；不写业务库、不上传、不下单。
密钥只从环境变量 GMGN_API_KEY 读取，绝不写入任何文件或日志。
"""
import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/opt/meme-radar")
from gmgn import Client, rank_items

ROUNDS      = int(os.environ.get("SPACING_ROUNDS", "12"))
SPACING_SEC = float(os.environ.get("SPACING_SEC", "5.0"))
MIN_SEC     = float(os.environ.get("SPACING_MIN_SEC", "15"))
PER_ROUND   = int(os.environ.get("SPACING_PER_ROUND", "3"))
CHAINS      = [c for c in os.environ.get("MEME_CHAINS", "bsc,solana,robinhood").split(",") if c]
OUT_DIR     = Path(os.environ.get("SPACING_OUT", "/var/lib/meme-radar/spacing-c2"))
API_KEY     = os.environ.get("GMGN_API_KEY", "").strip()


def run_round(idx: int) -> dict:
    t0 = time.time()
    calls, errors, candidates = [], [], []
    plan = (CHAINS * PER_ROUND)[:PER_ROUND]
    for chain in plan:
        client = Client(API_KEY, timeout=10, max_requests=1)  # 每次调用独立预算
        at = round(time.time(), 3)
        try:
            data = client.trending(chain, interval="1h", limit=100)
            items = rank_items(data)
            candidates.append(len(items))
            calls.append({"chain": chain, "at": at, "ok": True, "items": len(items)})
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            errors.append({"chain": chain, "at": at, "error": code})
            calls.append({"chain": chain, "at": at, "ok": False, "error": code})
        time.sleep(SPACING_SEC)
    elapsed = time.time() - t0
    if elapsed < MIN_SEC:
        time.sleep(MIN_SEC - elapsed)
    return {"round": idx, "started_utc": datetime.fromtimestamp(t0, timezone.utc).isoformat(),
            "elapsed_sec": round(time.time() - t0, 3), "gmgn_calls": len(plan),
            "errors": errors, "candidates": candidates, "calls": calls}


def main() -> int:
    if not API_KEY:
        print("GMGN_API_KEY 缺失，退出", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = []
    for i in range(1, ROUNDS + 1):
        r = run_round(i)
        rows.append(r)
        (OUT_DIR / f"run-{run_id}-round-{i:02d}.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1) + "\n")
        print(json.dumps({"round": i, "elapsed_sec": r["elapsed_sec"],
                          "calls": r["gmgn_calls"], "errors": len(r["errors"])}), flush=True)
    total_calls = sum(r["gmgn_calls"] for r in rows)
    errs = [e for r in rows for e in r["errors"]]
    r429 = sum(1 for e in errs if "429" in str(e["error"]))
    cands = sorted(c for r in rows for c in r["candidates"])
    median = cands[len(cands) // 2] if cands else None
    summary = {"run_id": run_id, "rounds": len(rows), "spacing_sec": SPACING_SEC,
               "per_round_calls": PER_ROUND, "gmgn_calls_total": total_calls,
               "errors_total": len(errs), "rate_429": round(r429 / total_calls, 4) if total_calls else None,
               "candidates_median": median,
               "files": sorted(p.name for p in OUT_DIR.glob(f"run-{run_id}-*"))}
    (OUT_DIR / f"run-{run_id}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
