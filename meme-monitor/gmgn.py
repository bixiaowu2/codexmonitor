"""Optional GMGN read-only enrichment for Meme Radar.

This module intentionally implements only the query-authenticated GMGN API
surface. It never loads a private key and never calls trading endpoints.
"""
from __future__ import annotations

import json
import time
import uuid
import urllib.parse
import urllib.request
from typing import Any

from net import FetchError

BASE_URL = "https://openapi.gmgn.ai"
SUPPORTED_CHAINS = ("sol", "bsc", "base", "eth", "arbitrum", "hyperevm",
                    "robinhood", "arc", "stable")
CHAIN_MAP = {"solana": "sol", "xlayer": "hyperevm"}


def gmgn_chain(chain: str) -> str:
    return CHAIN_MAP.get(str(chain).lower(), str(chain).lower())


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _price(info: dict[str, Any]) -> float | None:
    value = info.get("price")
    if isinstance(value, dict):
        value = value.get("price")
    price = _number(value if value is not None else info.get("price_usd"))
    return price if price is not None and price > 0 else None


def _payload(raw: Any) -> Any:
    if not isinstance(raw, dict):
        raise FetchError("gmgn_invalid_schema")
    if raw.get("code") not in (0, "0", None):
        raise FetchError("gmgn_code_" + str(raw.get("code")))
    data = raw.get("data", raw)
    return data


def _row(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        for key in ("token", "token_info", "result"):
            if isinstance(data.get(key), dict):
                return data[key]
        return data
    return {}


class Client:
    """Small query-only client using GMGN's official existing-auth protocol."""

    def __init__(self, api_key: str, timeout: int = 10, max_requests: int = 16,
                 base_url: str = BASE_URL):
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.max_requests = max_requests
        self.base_url = base_url.rstrip("/")
        self.requests = 0
        self.errors = 0
        self.cache_hits = 0
        self.backoff_until = 0.0
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.enabled:
            raise FetchError("gmgn_disabled")
        if self.requests >= self.max_requests:
            raise FetchError("gmgn_budget")
        if self.backoff_until > time.time():
            raise FetchError("gmgn_backoff")
        query = dict(params)
        query["timestamp"] = int(time.time())
        query["client_id"] = str(uuid.uuid4())
        url = self.base_url + path + "?" + urllib.parse.urlencode(query, doseq=True)
        request = urllib.request.Request(url, headers={
            "X-APIKEY": self.api_key,
            "User-Agent": "meme-radar-gmgn-readonly/1",
            "Accept": "application/json",
        })
        self.requests += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read(4_000_000))
        except Exception as exc:
            self.errors += 1
            code = getattr(exc, "code", None)
            if code == 429:
                self.backoff_until = time.time() + 120
                self.last_error = "http_429"
                raise FetchError("gmgn_http_429") from None
            self.last_error = "http_" + str(code) if code else type(exc).__name__
            raise FetchError("gmgn_" + self.last_error) from None
        try:
            return _payload(body)
        except FetchError:
            self.errors += 1
            self.last_error = "invalid_response"
            raise

    def token_info(self, chain: str, address: str) -> dict[str, Any]:
        return _row(self._get("/v1/token/info", {"chain": gmgn_chain(chain), "address": address}))

    def token_security(self, chain: str, address: str) -> dict[str, Any]:
        return _row(self._get("/v1/token/security", {"chain": gmgn_chain(chain), "address": address}))

    def trending(self, chain: str, interval: str = "1h", limit: int = 20) -> Any:
        return self._get("/v1/market/rank", {
            "chain": gmgn_chain(chain), "interval": interval,
            "limit": min(max(int(limit), 1), 100), "order_by": "volume",
            "direction": "desc",
        })

    def enrich(self, pairs: list[dict[str, Any]], max_tokens: int = 8) -> dict[str, Any]:
        """Enrich the most liquid candidates; no GMGN value becomes a score bonus."""
        if not self.enabled:
            return {"status": "disabled", "requests": 0, "errors": 0, "enriched": 0}
        candidates = sorted(pairs, key=lambda p: (
            -(p.get("liquidity_usd") or 0), -(p.get("volume_1h") or 0)))
        enriched = 0
        for pair in candidates[:max_tokens]:
            chain, address = pair.get("chain"), pair.get("address")
            if not chain or not address or gmgn_chain(chain) not in SUPPORTED_CHAINS:
                continue
            item: dict[str, Any] = {"chain": gmgn_chain(chain), "address": address,
                                    "checked_at": time.time()}
            try:
                info = self.token_info(chain, address)
                if info.get("address") and str(info["address"]).lower() != str(address).lower():
                    raise FetchError("gmgn_address_mismatch")
                security = self.token_security(chain, address)
                item["info"] = info
                item["security"] = security
                # Keep source values separate from Gecko/DexScreener values.
                item["price_usd"] = _price(info)
                for source_key, target_key in (("liquidity", "gmgn_liquidity_usd"),
                                                ("market_cap", "gmgn_market_cap"),
                                                ("marketcap", "gmgn_market_cap")):
                    value = _number(info.get(source_key))
                    if value is not None:
                        item[target_key] = value
                pair["gmgn"] = item
                if item.get("price_usd") is not None and not pair.get("price_usd"):
                    pair["price_usd"] = item["price_usd"]
                enriched += 1
            except FetchError as exc:
                item["error"] = exc.code
                pair["gmgn"] = item
                if exc.code in ("gmgn_http_429", "gmgn_backoff", "gmgn_budget"):
                    break
        status = "partial" if self.errors else ("ok" if enriched else "empty")
        return {"status": status, "requests": self.requests, "errors": self.errors,
                "enriched": enriched, "last_error": self.last_error,
                "retry_at": self.backoff_until or None}
