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


def _addr(chain, value):
    value = str(value or '').strip()
    return value if str(chain).lower() == 'solana' else value.lower()


def rank_items(data):
    """Extract the token list from a /v1/market/rank response (tolerant of nesting)."""
    if not isinstance(data, dict):
        return []
    inner = data.get('data', data)
    if isinstance(inner, dict):
        if isinstance(inner.get('rank'), list):
            return inner['rank']
        deeper = inner.get('data')
        if isinstance(deeper, dict) and isinstance(deeper.get('rank'), list):
            return deeper['rank']
    return []


def normalize_rank_item(item, internal_chain):
    """Convert a GMGN market-rank item into a radar pair dict.

    The rank endpoint carries the exact safety fields the radar otherwise reports
    missing (buy_tax/sell_tax/honeypot/holders/lock). They are stored under
    `gmgn_rank` for the safety merge step; discovery scoring uses the standard
    price/liquidity/volume/market_cap fields only.
    """
    if not isinstance(item, dict) or not item.get('address'):
        return None
    chain = str(internal_chain).lower()
    token = _addr(chain, item['address'])
    exchange = item.get('exchange')
    pair = _addr(chain, exchange) if exchange else token
    p = {
        'chain': chain,
        'source': 'gmgn',
        'address': token,
        'symbol': item.get('symbol') or '?',
        'name': item.get('name') or '',
        'pair_address': pair,
        'dex': '',
        'url': 'https://gmgn.ai/' + gmgn_chain(chain) + '/token/' + token,
        'created_at': _number(item.get('creation_timestamp')),
        'price_usd': _number(item.get('price')),
        'liquidity_usd': _number(item.get('liquidity')),
        'market_cap': _number(item.get('market_cap')),
        'fetched_at': time.time(),
        'volume_1h': _number(item.get('volume')),
        'buys_1h': _number(item.get('buys')),
        'sells_1h': _number(item.get('sells')),
        'endpoint': 'market_rank',
        'gmgn_rank': {
            'buy_tax': item.get('buy_tax'),
            'sell_tax': item.get('sell_tax'),
            'is_honeypot': item.get('is_honeypot'),
            'is_renounced': item.get('is_renounced'),
            'is_open_source': item.get('is_open_source'),
            'lock_percent': item.get('lock_percent'),
            'holder_count': item.get('holder_count'),
            'top_10_holder_rate': item.get('top_10_holder_rate'),
            'rug_ratio': item.get('rug_ratio'),
            'smart_degen_count': item.get('smart_degen_count'),
            'renowned_count': item.get('renowned_count'),
            'bundler_rate': item.get('bundler_rate'),
            'swaps': item.get('swaps'),
            'checked_at': time.time(),
        },
    }
    return p


def trending_pairs(client, chains, limit=10):
    """Fetch GMGN market-rank trending per chain as additional discovery pairs."""
    out = []
    health = {'status': 'disabled', 'requests': 0, 'errors': 0, 'pairs': 0, 'per_chain': {}}
    if not client.enabled:
        return out, health
    for chain in chains:
        chain = str(chain).lower()
        if gmgn_chain(chain) not in SUPPORTED_CHAINS:
            continue
        try:
            time.sleep(2.0)
            data = client.trending(chain, interval='1h', limit=limit)
            items = rank_items(data)
            for item in items:
                p = normalize_rank_item(item, chain)
                if p:
                    out.append(p)
            health['per_chain'][chain] = {'count': len(items)}
        except FetchError as exc:
            health['per_chain'][chain] = {'error': exc.code}
            health['errors'] += 1
    health['pairs'] = len(out)
    health['requests'] = client.requests
    health['errors'] = client.errors
    health['status'] = 'ok' if out else ('empty' if not health['errors'] else 'partial')
    return out, health
