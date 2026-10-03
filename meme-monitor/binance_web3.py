"""Read-only Binance Web3 Skills evidence bridge.

Binance Web3 Skills are CLI-backed and their authenticated endpoints vary by
installation.  The radar consumes a small, auditable JSONL hand-off instead
of guessing private endpoint URLs.  A local skill/CLI wrapper may write this
file; the radar only validates and displays evidence, never trades.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from sources import address

CHAIN_IDS = {"bsc": "56", "solana": "CT_501", "base": "8453", "ethereum": "1"}
SUPPORTED_LIVE = set(CHAIN_IDS)
WEB3 = "https://web3.binance.com"


def _request(url: str, method: str = "GET", body: dict[str, Any] | None = None,
             timeout: int = 8) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": "binance-web3/2.0 (Skill)", "Accept-Encoding": "identity",
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read(3_000_000))
        if not isinstance(payload, dict) or payload.get("success") is False or payload.get("code") not in (None, 0, "000000"):
            raise RuntimeError("upstream_business_error")
        if not isinstance(payload.get("data"), dict) or not payload["data"]:
            raise RuntimeError("upstream_data_missing")
        return payload["data"]
    except urllib.error.HTTPError as exc:
        raise RuntimeError("http_" + str(exc.code)) from None
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(type(exc).__name__) from None


def live_enrich(pairs: list[dict[str, Any]], max_tokens: int = 4,
                timeout: int = 8) -> dict[str, Any]:
    """Query Binance's documented public Web3 info and audit endpoints.

    This is intentionally bounded and query-only. Unsupported chains remain in
    the existing Gecko/GoPlus path with an explicit coverage gap.
    """
    candidates = [p for p in pairs if str(p.get("chain")).lower() in SUPPORTED_LIVE]
    candidates.sort(key=lambda p: (-(p.get("liquidity_usd") or 0), -(p.get("volume_1h") or 0)))
    requests = errors = enriched = 0
    last_error = ""
    for pair in candidates[:max(0, int(max_tokens))]:
        chain = str(pair.get("chain")).lower(); chain_id = CHAIN_IDS[chain]
        address_value = pair.get("address")
        item = {"chain": chain, "address": address_value, "provider": "binance_web3_skills",
                "checked_at": time.time()}
        dynamic_url = (WEB3 + "/bapi/defi/v4/public/wallet-direct/buw/wallet/market/"
                       "token/dynamic/info/ai?chainId=" + urllib.parse.quote(chain_id) +
                       "&contractAddress=" + urllib.parse.quote(str(address_value)))
        for kind, url, method, body in (
            ("info", dynamic_url, "GET", None),
            ("audit", WEB3 + "/bapi/defi/v1/public/wallet-direct/security/token/audit",
             "POST", {"binanceChainId": chain_id, "contractAddress": address_value,
                      "requestId": str(uuid.uuid4())}),
        ):
            requests += 1
            try:
                result = _request(url, method=method, body=body, timeout=timeout)
                if kind == "audit" and (result.get("hasResult") is not True or result.get("isSupported") is not True):
                    raise RuntimeError("audit_unavailable")
                if kind == "info":
                    try:
                        price = float(result.get("price"))
                    except (TypeError, ValueError):
                        price = 0
                    if not 0 < price < float("inf"):
                        raise RuntimeError("info_price_missing")
                item[kind] = result
            except Exception as exc:
                errors += 1; last_error = str(exc)
        if "info" in item or "audit" in item:
            pair["binance_web3"] = item
            pair["evidence_sources"] = list(dict.fromkeys((pair.get("evidence_sources") or []) + ["binance_web3_skills"]))
            enriched += 1
    status = "partial" if errors else ("ok" if enriched else "empty")
    return {"status": status, "provider": "binance_web3_skills", "requests": requests,
            "errors": errors, "enriched": enriched, "last_error": last_error,
            "supported_chains": sorted(SUPPORTED_LIVE)}


def _valid(item: Any, now: float, max_age: int) -> bool:
    if not isinstance(item, dict) or not item.get("chain") or not item.get("address"):
        return False
    try:
        age = now - float(item.get("checked_at", item.get("observed_at", 0)))
    except (TypeError, ValueError):
        return False
    return 0 <= age <= max_age and bool(item.get("provider", "").startswith("binance"))


def read(path: str | Path | None, pairs: list[dict[str, Any]], now: float | None = None,
         max_age: int = 1800) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    now = time.time() if now is None else now
    if not path:
        return {}, {"status": "disabled", "provider": "binance_web3_skills", "matched": 0}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()[-5000:]
    except OSError:
        return {}, {"status": "unavailable", "provider": "binance_web3_skills", "matched": 0}
    index = {(p.get("chain"), p.get("address")): p for p in pairs}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    invalid = 0
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            invalid += 1
            continue
        if not _valid(item, now, max_age):
            invalid += 1
            continue
        key = (str(item["chain"]).lower(), address(str(item["chain"]), item["address"]))
        if key not in index:
            continue
        old = out.get(key)
        stamp = float(item.get("checked_at", item.get("observed_at", 0)))
        if old is None or stamp > old["checked_at"]:
            item["checked_at"] = stamp
            out[key] = item
    status = "ok" if out else ("empty" if not invalid else "partial")
    return out, {"status": status, "provider": "binance_web3_skills", "matched": len(out),
                 "invalid_or_stale": invalid, "as_of": now, "max_age": max_age}


def apply(pairs: list[dict[str, Any]], evidence: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    for pair in pairs:
        item = evidence.get((pair.get("chain"), pair.get("address")))
        if item:
            pair["binance_web3"] = item
            pair["evidence_sources"] = list(dict.fromkeys((pair.get("evidence_sources") or []) + ["binance_web3_skills"]))
    return pairs


def audit_flags(pair: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Translate Binance audit hits into conservative local safety labels."""
    audit = (pair.get("binance_web3") or {}).get("audit")
    if not isinstance(audit, dict):
        return [], []
    if isinstance(audit.get("data"), dict):
        audit = audit["data"]
    if audit.get("hasResult") is False or audit.get("isSupported") is False:
        return [], ["Binance Web3审计不支持或无结果"]
    blocks: list[str] = []
    warnings: list[str] = []
    level = str(audit.get("riskLevelEnum", "")).upper()
    if level == "HIGH" or (isinstance(audit.get("riskLevel"), (int, float)) and audit["riskLevel"] >= 4):
        blocks.append("Binance Web3审计高风险")
    for group in audit.get("riskItems", []) if isinstance(audit.get("riskItems"), list) else []:
        for detail in group.get("details", []) if isinstance(group, dict) else []:
            if not isinstance(detail, dict) or detail.get("isHit") is not True:
                continue
            label = str(detail.get("title") or group.get("id") or "Binance Web3风险")
            (blocks if str(detail.get("riskType", "")).upper() == "RISK" else warnings).append(label)
    return list(dict.fromkeys(blocks)), list(dict.fromkeys(warnings))
