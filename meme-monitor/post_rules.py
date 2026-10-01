"""Predeclared user-post filters; observation only, no score or order changes."""
import math

VERSION = 'early-microcap-observe-v1'


def number(value):
    try:
        n = float(value)
        return n if not isinstance(value, bool) and math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def evaluate(pair, now):
    created = number(pair.get('created_at'))
    if created is not None and created > 1e12:
        created /= 1000
    age = now - created if created is not None else None
    cap = number(pair.get('market_cap'))
    liquidity = number(pair.get('liquidity_usd'))
    volume = number(pair.get('volume_6h'))
    values = {'pool_age': age, 'market_cap': cap, 'liquidity': liquidity, 'volume_6h': volume}
    missing = [key for key, value in values.items() if value is None or value < 0]
    matched = not missing and 300 <= age <= 86400 and 0 < cap < 50000 and liquidity > 10000 and volume > 10000
    return {'version': VERSION, 'status': 'unknown' if missing else 'matched' if matched else 'not_matched',
            'missing': missing, 'matched': matched, 'score_bonus': 0,
            'observation_eligible': matched and pair.get('safety_eligible') is True,
            'second_wave': 'unknown_without_prior_peak_unique_buyers_and_history',
            'volume_authenticity': 'unverified', 'price_trade_timestamp': 'unknown',
            'source': 'https://x.com/laoyingkhq/status/2105575947344687352'}
