"""Parsers for the read-only Binance Alpha data documented by Binance Skills.

The adapter deliberately contains no authentication or trading code.  The
public Alpha REST responses have changed shape in the past, so callers use the
returned diagnostics instead of treating a malformed response as a quote.
"""
from __future__ import annotations

import time
from typing import Any


def _number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if value == value and abs(value) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("data", payload)
    if isinstance(payload, dict):
        for key in ("data", "list", "rows", "result"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return [payload] if isinstance(payload, dict) else []


def ticker_quote(payload: Any, alpha_id: str, now: float | None = None,
                 max_age_ms: int = 180_000) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Parse an official Alpha ticker and reject stale/future data."""
    now = time.time() if now is None else now
    symbol = str(alpha_id).upper() + "USDT"
    rows = _rows(payload)
    row = next((r for r in rows if str(r.get("symbol", symbol)).upper() == symbol), None)
    if row is None and len(rows) == 1 and not rows[0].get("symbol"):
        row = rows[0]
    if row is None:
        return None, {"provider": "binance_alpha_official", "reason": "symbol_not_found", "symbol": symbol}
    price = _number(row.get("lastPrice", row.get("price")))
    stamp = _number(row.get("closeTime", row.get("eventTime", row.get("time"))))
    detail = {"provider": "binance_alpha_official", "symbol": symbol,
              "last_quote_time": stamp, "checked_at": now}
    if price is None or price <= 0 or stamp is None:
        detail["reason"] = "schema_or_price_missing"
        return None, detail
    age = now * 1000 - stamp
    detail["age_seconds"] = round(age / 1000, 1)
    if age < -60_000 or age > max_age_ms:
        detail["reason"] = "stale_or_future"
        return None, detail
    detail["reason"] = "ok"
    return {"price": price, "time": stamp, "source": "binance_alpha_official",
            "provider": "binance_alpha_official"}, detail


def token_list(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize the documented Alpha token-list response."""
    rows = _rows(payload)
    valid = [r for r in rows if r.get("alphaId") and r.get("contractAddress")]
    return valid, {"provider": "binance_alpha_official", "count": len(valid),
                   "schema": "ok" if valid else "empty_or_changed"}
