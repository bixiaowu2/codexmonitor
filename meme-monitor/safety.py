"""Read-only third-party risk screening; never proof of an executable sell."""
from __future__ import annotations
import math
import re
import time
from urllib.parse import quote
from net import get_json, FetchError

# Verified against GoPlus /api/v1/supported_chains on 2026-09-27.
# 2020 is NOT Robinhood. Identity must be chain + exact contract, never symbol.
EVM_CHAIN_IDS = {'bsc': '56', 'xlayer': '196', 'robinhood': '4663', 'arc': '5042', 'stable': '988'}
POLICY_VERSION = 'goplus-screen-v1.1-coverage'


def flag(value):
    if isinstance(value, dict):
        value = value.get('status')
    if str(value).lower() in ('1', 'true'):
        return True
    if str(value).lower() in ('0', 'false'):
        return False
    return None


def fraction(value):
    try:
        number = float(value)
        # GoPlus percentages are fractions. Never guess units or accept NaN.
        return number if math.isfinite(number) and 0 <= number <= 1 else None
    except (TypeError, ValueError):
        return None


def top10(result):
    holders = result.get('holders')
    if not isinstance(holders, list) or not holders:
        return None
    values = []
    seen = set()
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        address = holder.get('address') or holder.get('account')
        if address and address in seen:
            continue
        if address:
            seen.add(address)
        tag = str(holder.get('tag') or '').lower()
        if flag(holder.get('is_dex')) is True or tag in ('burn', 'burn address', 'null address', 'dead', 'lp', 'liquidity pool'):
            continue
        if str(address).lower() in ('0x0000000000000000000000000000000000000000', '0x000000000000000000000000000000000000dead'):
            continue
        value = fraction(holder.get('percent'))
        if value is not None:
            values.append(value)
    total = sum(sorted(values, reverse=True)[:10])
    return total if values and total <= 1.000001 else None


def unknown(reason, provider='goplus'):
    return {'status': 'unknown', 'blocks': [], 'warnings': [reason], 'provider': provider,
            'eligible': False, 'policy': POLICY_VERSION, 'sell_simulated': False,
            'coverage_status': 'unavailable'}


def assess(chain, result):
    if not isinstance(result, dict) or not result:
        return unknown('安全接口缺少有效字段')
    blocks, warnings, missing = [], [], []
    if chain == 'solana':
        required = ('non_transferable', 'freezable', 'closable', 'balance_mutable_authority',
                    'mintable', 'transfer_fee_upgradable', 'transfer_hook_upgradable', 'default_account_state_upgradable')
        block_flags = {'non_transferable': '代币不可转账', 'freezable': '发行方可冻结代币账户',
                       'closable': '发行方可关闭代币账户', 'balance_mutable_authority': '权限方可修改余额'}
        warn_flags = {'mintable': '仍存在增发权限', 'transfer_hook_upgradable': '转账Hook可升级',
                      'transfer_fee_upgradable': '转账费率可升级',
                      'default_account_state_upgradable': '默认账户状态可修改'}
        if not isinstance(result.get('transfer_hook'), list):
            missing.append('transfer_hook')
        if not isinstance(result.get('transfer_fee'), dict):
            missing.append('transfer_fee')
        elif result['transfer_fee']:
            warnings.append('存在转账费配置，实际卖出费用需独立验证')
        if str(result.get('default_account_state')) not in ('1', '2'):
            missing.append('default_account_state')
        if result.get('transfer_hook'):
            warnings.append('存在转账Hook，需独立验证')
        if str(result.get('default_account_state')) == '2':
            blocks.append('新账户默认为冻结状态')
    elif chain in EVM_CHAIN_IDS:
        required = ('is_honeypot', 'cannot_sell_all', 'cannot_buy', 'transfer_pausable',
                    'is_blacklisted', 'owner_change_balance', 'is_open_source', 'is_proxy',
                    'is_mintable', 'slippage_modifiable', 'personal_slippage_modifiable')
        block_flags = {'is_honeypot': '接口判定为蜜罐', 'cannot_sell_all': '接口判定无法完整卖出',
                       'cannot_buy': '接口判定无法买入', 'transfer_pausable': '转账可被暂停',
                       'is_blacklisted': '存在黑名单权限', 'owner_change_balance': '权限方可修改持有人余额',
                       'selfdestruct': '存在自毁权限'}
        warn_flags = {'can_take_back_ownership': '所有权可回收', 'hidden_owner': '存在隐藏所有权',
                      'is_mintable': '仍存在增发权限', 'is_proxy': '代理合约可能升级',
                      'slippage_modifiable': '税费可修改', 'personal_slippage_modifiable': '个人税费可修改'}
        if flag(result.get('is_open_source')) is False:
            warnings.append('合约未开源')
        for key, label in (('sell_tax', '卖出税'), ('buy_tax', '买入税')):
            value = fraction(result.get(key))
            if value is None:
                missing.append(key)
            elif key == 'sell_tax' and value >= .20:
                blocks.append(f'{label}达到{value:.1%}')
            elif value >= .10:
                warnings.append(f'{label}达到{value:.1%}')
    else:
        return unknown('当前链未接入安全检查', 'unsupported')
    for key in set(required) | set(block_flags) | set(warn_flags):
        if flag(result.get(key)) is None:
            missing.append(key)
    for key, label in block_flags.items():
        if flag(result.get(key)) is True:
            blocks.append(label)
    for key, label in warn_flags.items():
        if flag(result.get(key)) is True:
            warnings.append(label)
    share = top10(result)
    coverage_missing = list(missing)
    if share is None:
        warnings.append('持仓集中度数据缺失或无效')
        coverage_missing.append('holders')
    elif share >= .90:
        blocks.append(f'已报告前十大户持仓集中约{share:.1%}（排除已标记池/销毁地址）')
    elif share >= .50:
        warnings.append(f'已报告前十大户持仓集中约{share:.1%}（排除已标记池/销毁地址）')
    if missing:
        missing=sorted(set(missing))
        warnings.append('关键安全字段缺失：' + ','.join(missing))
    status = 'blocked' if blocks else 'unknown' if missing else 'safe'
    return {'status': status, 'blocks': blocks, 'warnings': warnings, 'missing': missing,
            'coverage_status': 'partial_fields' if coverage_missing else 'complete_reported_fields',
            'coverage_missing': sorted(set(coverage_missing)),
            'top10_share': share, 'provider': 'goplus', 'eligible': status == 'safe' and not warnings,
            'policy': POLICY_VERSION, 'sell_simulated': False}


class Checker:
    def __init__(self, enabled=False, timeout=10, cache=None, cache_seconds=900,
                 max_checks=12, budget_seconds=30):
        self.enabled, self.timeout = enabled, timeout
        self.cache = cache if isinstance(cache, dict) else {}
        self.cache_seconds, self.max_checks = cache_seconds, max_checks
        self.deadline = time.monotonic() + max(0, budget_seconds)
        self.requests = 0
        self.api_errors = 0
        self.cache_hits = 0
        self.backoff_until = self.cache.pop('_backoff_until', 0)
        self.backoff_reason = self.cache.pop('_backoff_reason', 'provider_error')
        now = time.time()
        self.cache = {k: v for k, v in self.cache.items() if isinstance(v, dict)
                      and isinstance(v.get('checked_at'), (int, float))
                      and 0 <= now - v['checked_at'] < 86400 and v.get('policy') == POLICY_VERSION}

    def check(self, pair):
        if not self.enabled:
            return {'status': 'disabled', 'blocks': [], 'warnings': ['安全检查未启用'],
                    'provider': 'disabled', 'eligible': False}
        chain, addr = pair.get('chain', ''), pair.get('address', '')
        if chain not in EVM_CHAIN_IDS and chain != 'solana':
            return unknown('当前链未接入安全检查', 'unsupported')
        pattern = r'[1-9A-HJ-NP-Za-km-z]{32,44}' if chain == 'solana' else r'0x[0-9a-fA-F]{40}'
        if not isinstance(addr, str) or not re.fullmatch(pattern, addr):
            return unknown('合约地址格式无效', 'identity')
        if chain != 'solana':
            addr = addr.lower()
        key = chain + ':' + addr
        old = self.cache.get(key)
        if old:
            ttl = min(60, self.cache_seconds) if old.get('api_error') else self.cache_seconds
            if 0 <= time.time() - old['checked_at'] < ttl:
                self.cache_hits += 1
                return old
        remaining = self.deadline - time.monotonic()
        if self.requests >= self.max_checks or remaining < 1:
            return unknown('本轮安全检查预算已用完', 'budget')
        if self.backoff_until > time.time():
            label='安全接口限流（HTTP 429）' if self.backoff_reason=='http_429' else '安全接口暂时不可用（'+self.backoff_reason+'）'
            out=unknown(label+'，预计 '+time.strftime('%H:%M:%S UTC',time.gmtime(self.backoff_until))+' 后重试', 'backoff')
            out.update(retry_at=self.backoff_until, api_error=self.backoff_reason)
            return out
        self.requests += 1
        try:
            endpoint = 'solana/token_security' if chain == 'solana' else 'token_security/' + EVM_CHAIN_IDS[chain]
            raw = get_json('https://api.gopluslabs.io/api/v1/' + endpoint + '?contract_addresses=' + quote(addr, safe=''), min(self.timeout, remaining))
            if isinstance(raw,dict) and str(raw.get('code'))=='429':raise FetchError('http_429')
            if not isinstance(raw, dict) or str(raw.get('code')) != '1' or not isinstance(raw.get('result'), dict):
                raise FetchError('invalid_schema_or_api_rejected')
            # Case-insensitive EVM matching; never lowercase a Solana mint.
            results = raw['result'] if chain == 'solana' else {k.lower(): v for k, v in raw['result'].items()}
            result = results.get(addr)
            if not isinstance(result, dict) or not result:
                raise FetchError('missing_result')
            out = assess(chain, result)
        except Exception as exc:
            self.api_errors += 1
            code = exc.code if isinstance(exc, FetchError) else type(exc).__name__
            out = unknown('供应商未返回该合约的安全数据' if code=='missing_result' else '安全接口不可用：' + code)
            out['api_error'] = code
            # Missing coverage or a malformed token result is not a provider-wide outage.
            systemic=(code=='http_429' or code.startswith('http_5') or code in
                      ('TimeoutError','URLError','ConnectionResetError','ConnectionError','timeout'))
            if systemic:
                self.backoff_until = time.time() + max(60,getattr(exc,'retry_after',None) or 0)
                self.backoff_reason = code
                out['retry_at'] = self.backoff_until
        out.update(checked_at=time.time(), address=addr, chain=chain)
        self.cache[key] = out
        return out

    def snapshot(self):
        entries = sorted(self.cache.items(), key=lambda kv: kv[1]['checked_at'], reverse=True)[:2000]
        return dict(entries, _backoff_until=self.backoff_until, _backoff_reason=self.backoff_reason)
