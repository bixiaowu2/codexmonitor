"""User-post hypotheses evaluated against optional audited wallet evidence, never a score bonus."""
import math
import re
from urllib.parse import urlsplit

VERSION = 'wallet-convergence-observe-v1'
CHAIN_HOSTS = {'bsc': {'bscscan.com'}, 'solana': {'solscan.io'}}


def numeric(v):
    try:
        n = float(v)
        return n if not isinstance(v, bool) and math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def valid_address(chain, value):
    pattern = r'[1-9A-HJ-NP-Za-km-z]{32,44}' if chain == 'solana' else r'0x[0-9a-fA-F]{40}'
    return isinstance(value, str) and bool(re.fullmatch(pattern, value))


def evidence(pair, wallets, now):
    """Input claims are not independently chain-verified here; show provenance, refuse incomplete claims."""
    chain = pair['chain']; token = pair['address']; candidates = {}; missing = set()
    for event in wallets.values():
        if not isinstance(event, dict) or event.get('chain') != chain:
            continue
        normalize = (lambda value: value) if chain == 'solana' else (lambda value: str(value or '').lower())
        if normalize(event.get('token')) != normalize(token):
            continue
        wallet = event.get('address')
        if not valid_address(chain, wallet) or not valid_address(chain, token):
            missing.add('完整钱包或代币地址无效'); continue
        observed = numeric(event.get('observed_at')); active = numeric(event.get('last_active_at'))
        if observed is None or not 0 <= now - observed <= 3600:
            missing.add('缺少一小时内持有证据'); continue
        if active is None or not 0 <= now - active <= 30 * 86400:
            missing.add('缺少近30天活跃证据'); continue
        intervals = event.get('recent_transaction_times')
        if not isinstance(intervals, list) or len(intervals) < 6 or any(numeric(v) is None for v in intervals):
            missing.add('机器人频率尚未核验'); continue
        ordered = sorted(set(float(v) for v in intervals))
        if len(ordered) < 6 or ordered[-1] > now or ordered[0] < now - 30 * 86400:
            missing.add('交易时序无效'); continue
        gaps = sorted(b - a for a, b in zip(ordered, ordered[1:]))
        if gaps[len(gaps) // 2] <= 10:
            missing.add('秒级交易钱包已排除'); continue
        try:
            parsed = urlsplit(str(event.get('source') or event.get('url') or ''))
        except ValueError:
            missing.add('交易来源网址无效'); continue
        tx = str(event.get('tx_hash') or '')
        tx_pattern = r'[1-9A-HJ-NP-Za-km-z]{64,100}' if chain == 'solana' else r'0x[0-9a-fA-F]{64}'
        if parsed.scheme != 'https' or parsed.username or parsed.hostname not in CHAIN_HOSTS.get(chain, set()) or not re.fullmatch(tx_pattern, tx) or parsed.path != '/tx/' + tx:
            missing.add('缺少可信浏览器的交易来源'); continue
        if event.get('action') not in ('buy', 'sell') or event.get('confirmed_swap') is not True:
            missing.add('未证明是成交而非转账'); continue
        amount = numeric(event.get('token_amount')); balance = numeric(event.get('remaining_balance'))
        if amount is None or amount <= 0 or balance is None or balance < 0:
            missing.add('买卖数量或剩余持仓未知'); continue
        cluster = event.get('independence_cluster')
        independence = event.get('independence_source')
        if not isinstance(cluster, str) or not cluster.strip() or not isinstance(independence, str) or not independence.startswith('https://'):
            missing.add('钱包关联性尚未核验'); continue
        key = normalize(wallet)
        if key not in candidates or observed > candidates[key]['observed_at']:
            candidates[key] = dict(event, observed_at=observed)
    buys = [e for e in candidates.values() if e['action'] == 'buy' and numeric(e['remaining_balance']) > 0]
    sells = [e for e in candidates.values() if e['action'] == 'sell']
    groups = {e['independence_cluster'] for e in buys}
    convergence = len(groups) >= 3
    eligible = convergence and pair.get('safety_eligible') is True and not sells
    return {'version': VERSION, 'status': 'provided_evidence' if candidates else 'unknown',
            'independent_buy_clusters': len(groups), 'wallets_still_holding': len(buys), 'recent_selling_wallets': len(sells),
            'convergence': convergence, 'observation_eligible': eligible, 'missing': sorted(missing),
            'sources': [e.get('source') or e.get('url') for e in candidates.values()][:5],
            'verification': 'provided_audited_evidence_not_independently_fetched',
            'score_bonus': 0, 'historical_skill': 'unknown_without_full_realized_wins_and_losses'}


def render(result):
    if result['status'] == 'unknown':
        return '钱包交叉观察：暂无合格交易证据，未进行自动聪明钱包全链采集；不加买入分。'
    return (f'钱包交叉观察：据提供资料，独立买入组{result["independent_buy_clusters"]}，仍持有钱包{result["wallets_still_holding"]}，近期卖出钱包{result["recent_selling_wallets"]}；'
            '尚未独立回查链上，历史能力待核验，不加买入分。')
