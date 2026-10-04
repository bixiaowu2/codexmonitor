"""Read-only Binance Web3 crypto-market-rank evidence for Alpha Radar.

The rank response is a separate market-data source.  It never replaces the
official Alpha ticker, and it never changes a safety gate by itself.
"""
from __future__ import annotations

import time
from typing import Any

RANK_URL = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list/ai"
RANK_TYPE = 20
DEFAULT_MAX_AGE = 900


def _number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if value == value and abs(value) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _valid_address(value: Any) -> str | None:
    value = str(value or "").strip().lower()
    return value if value.startswith("0x") and len(value) >= 10 else None


def normalize(payload: Any, checked_at: float | None = None) -> dict[str, Any]:
    """Validate and normalize one rank envelope without accepting a business error."""
    if not isinstance(payload, dict):
        raise ValueError("rank_invalid_envelope")
    if payload.get("code") not in (None, 0, "0", "000000"):
        raise ValueError("rank_api_code_" + str(payload.get("code")))
    data = payload.get("data")
    rows = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("rank_tokens_missing")
    checked_at = time.time() if checked_at is None else checked_at
    entries = []
    for ordinal, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        address = _valid_address(row.get("contractAddress"))
        chain_id = str(row.get("chainId") or "")
        if not address or not chain_id:
            continue
        audit = row.get("auditInfo") if isinstance(row.get("auditInfo"), dict) else {}
        entries.append({
            "rank": ordinal,
            "chain_id": chain_id,
            "address": address,
            "symbol": str(row.get("symbol") or "")[:64],
            "name": str((row.get("metaInfo") or {}).get("name") or "")[:120],
            "price": _number(row.get("price")),
            "percent_change_1h": _number(row.get("percentChange1h")),
            "percent_change_24h": _number(row.get("percentChange24h")),
            "volume_1h": _number(row.get("volume1h")),
            "volume_24h": _number(row.get("volume24h")),
            "volume_1h_buy": _number(row.get("volume1hBuy")),
            "volume_1h_sell": _number(row.get("volume1hSell")),
            "liquidity": _number(row.get("liquidity")),
            "holders": _number(row.get("holders")),
            "holders_top10_percent": _number(row.get("holdersTop10Percent")),
            "audit_risk_level": audit.get("riskLevel"),
            "audit_risk_codes": list(audit.get("riskCodes") or []) if isinstance(audit.get("riskCodes") or [], list) else [],
            "checked_at": checked_at,
        })
    return {"source": "binance_crypto_market_rank", "rank_type": RANK_TYPE,
            "chain_id": "56", "checked_at": checked_at, "count": len(entries),
            "entries": entries}


def fetch(api, chain_id: str = "56", size: int = 500,
          max_age: int = DEFAULT_MAX_AGE) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch one cached public page through the radar PublicAPI wrapper."""
    checked_at = time.time()
    params = {"rankType": RANK_TYPE, "chainId": str(chain_id), "page": 1,
              "size": min(max(int(size), 1), 500)}
    try:
        payload = api.get(RANK_URL, params)
        snapshot = normalize(payload, checked_at)
        health = {"status": "ok", "count": snapshot["count"], "matched": 0,
                  "checked_at": checked_at, "stale": False, "rank_type": RANK_TYPE}
        return snapshot, health
    except Exception as exc:
        return {"source": "binance_crypto_market_rank", "rank_type": RANK_TYPE,
                "chain_id": str(chain_id), "checked_at": checked_at, "count": 0,
                "entries": []}, {"status": "error", "count": 0, "matched": 0,
                "checked_at": checked_at, "stale": True,
                "error": str(exc)[:120], "rank_type": RANK_TYPE}


def attach(rows: list[dict[str, Any]], snapshot: dict[str, Any], now: float | None = None,
           max_age: int = DEFAULT_MAX_AGE) -> int:
    """Attach only exact chain/address matches; stale entries remain marked stale."""
    now = time.time() if now is None else now
    entries = {(str(x.get("chain_id")), str(x.get("address")).lower()): x
               for x in snapshot.get("entries", []) if isinstance(x, dict)}
    matched = 0
    for row in rows:
        key = (str(row.get("chain_id")), str(row.get("address") or "").lower())
        evidence = entries.get(key)
        if evidence is None:
            row["crypto_market_rank"] = {"status": "not_ranked", "source": "binance_crypto_market_rank"}
            continue
        evidence = dict(evidence)
        evidence["status"] = "stale" if now - float(evidence.get("checked_at", 0)) > max_age else "ok"
        row["crypto_market_rank"] = evidence
        matched += 1
    return matched
