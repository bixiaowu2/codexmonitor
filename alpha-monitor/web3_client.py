#!/usr/bin/env python3
"""币安 Web3 API 客户端(带地区代理)。

**为什么需要代理**: 这套 `/build/api/v1/*` 需要签名认证, 且服务器在 Oracle ap-tokyo-1(东京),
直连一律返回 `40304 compliance restriction`(日本是币安有条件限制地区)。
经 mihomo 走香港/新加坡/台湾节点后返回 `code:0 success`。

**代理原则(用户定的)**: 只有**地区受限**的调用才走代理。所以:
  - `/build/api/v1/*`  → 走代理(use_proxy=True)
  - 公开的 `/bapi/defi/v1/public/*`(meme-rush / trading-signal / crypto-market-rank 那套)
    → **直连**, 不需要代理

凭据: /etc/binance-web3.env (600, root)
代理: 默认 http://127.0.0.1:7890 (mihomo), 可用 WEB3_PROXY 覆盖; 设 use_proxy=False 可强制直连。
"""
from __future__ import annotations
import base64, hashlib, hmac, json, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENVF = Path('/etc/binance-web3.env')
DEFAULT_PROXY = 'http://127.0.0.1:7890'
BASE = 'https://web3.binance.com'

# 走代理会偶发 SSL 断连(实测 UNEXPECTED_EOF_WHILE_READING), 而这类失败是**瞬时的** ——
# 重试即可。不加会整个任务静默拿不到数据。次数/退避可调。
RETRIES = 3
BACKOFF = (1.0, 3.0)   # 每次重试前的等待秒数

CHAIN_BSC = '56'
CHAIN_SOL = 'CT_501'
CHAIN_BASE = '8453'
CHAIN_ETH = '1'


class Web3Error(RuntimeError):
    pass


def creds():
    env = {}
    try:
        for line in ENVF.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env.get('BINANCE_WEB3_API_KEY', ''), env.get('BINANCE_WEB3_API_SECRET', '')


def call(method, path, params=None, body=None, timeout=30, use_proxy=True):
    """签名调用(带重试)。path 不含 /build。

    走代理偶发 SSL 断连(瞬时), 故重试 RETRIES 次; 全部失败才抛 Web3Error。
    """
    last = None
    for attempt in range(RETRIES):
        try:
            return _call_once(method, path, params, body, timeout, use_proxy)
        except Web3Error as e:
            last = e
            msg = str(e)
            # 业务类错误(参数错/权限/地区)重试无意义, 直接抛
            if any(k in msg for k in ('code=40001', 'code=4010', 'code=4030', 'code=4041')):
                raise
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    raise last if last else Web3Error('unknown')


def _call_once(method, path, params=None, body=None, timeout=30, use_proxy=True):
    """单次签名调用。"""
    key, sec = creds()
    if not key or not sec:
        raise Web3Error('缺少 Web3 API 凭据(/etc/binance-web3.env)')
    req_path = '/build' + path
    if params:
        req_path += '?' + urllib.parse.urlencode(params)
    now = datetime.now(timezone.utc)
    ts = now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'
    payload = '' if method == 'GET' else json.dumps(body or {}, separators=(',', ':'), ensure_ascii=False)
    # 签名串: timestamp + METHOD + requestPath(含 /build, 原样未解码) + body(GET 为空)
    pre = ts + method + req_path + payload
    sig = base64.b64encode(hmac.new(sec.encode(), pre.encode(), hashlib.sha256).digest()).decode()
    req = urllib.request.Request(
        BASE + req_path,
        data=(payload.encode() if method != 'GET' else None),
        headers={'X-OC-APIKEY': key, 'X-OC-TIMESTAMP': ts, 'X-OC-SIGN': sig,
                 'Content-Type': 'application/json', 'Accept-Encoding': 'identity'},
        method=method)
    if use_proxy:
        import os
        proxy = os.environ.get('WEB3_PROXY', DEFAULT_PROXY)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    else:
        opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise Web3Error(f'http_{e.code}: {e.read()[:200].decode("utf-8", "ignore")}') from None
    except Exception as e:
        raise Web3Error(f'{type(e).__name__}: {str(e)[:150]}') from None
    if str(d.get('code')) not in ('0', '000000'):
        raise Web3Error(f"code={d.get('code')} {d.get('msg') or d.get('message') or ''}")
    return d.get('data')


def holders(chain_id, token_contract_address, **kw):
    """持币排行(最多 100 条)。每条含 holdingPercent / holdAmount / avgBuyPrice / maxHoldAmount。"""
    return call('GET', '/api/v1/dex/market/token/holder',
                {'binanceChainId': str(chain_id),
                 'tokenContractAddress': token_contract_address, **kw})


def top_traders(chain_id, token_contract_address, **kw):
    """盈利地址榜(按已实现盈亏降序, 最多 100 条)。"""
    return call('GET', '/api/v1/dex/market/token/top-trader',
                {'binanceChainId': str(chain_id),
                 'tokenContractAddress': token_contract_address, **kw})


def dev_info(chain_id, token_contract_address, **kw):
    """开发者画像: devLaunchedInfo(历史发币) + devHoldingInfo(当前持仓)。"""
    return call('GET', '/api/v1/dex/market/memepump/tokenDevInfo',
                {'binanceChainId': str(chain_id),
                 'tokenContractAddress': token_contract_address, **kw})


def top10_percent(chain_id, token_contract_address):
    """从持币排行算前十大集中度(百分比, 如 84.7 表示 84.7%)。

    比 GoPlus 更直接: 官方口径、每个地址都带 holdingPercent。
    注意: 这里**不剔除 LP/销毁地址**(币安返回的是原始排行), 所以数值会偏高,
    与 GoPlus 的'清洗后'口径不可直接比较。
    """
    rows = holders(chain_id, token_contract_address)
    if not isinstance(rows, list):
        return None
    vals = []
    for r in rows[:10]:
        try:
            vals.append(float(r.get('holdingPercent') or 0))
        except (TypeError, ValueError):
            pass
    return round(sum(vals), 4) if vals else None


def dev_holding_percent(chain_id, token_contract_address):
    """开发者当前持仓占比(%)。用于防 rug —— dev 持仓高意味着随时能砸盘。"""
    d = dev_info(chain_id, token_contract_address)
    if not isinstance(d, dict):
        return None
    h = d.get('devHoldingInfo')
    if isinstance(h, dict):
        for k in ('holdingPercent', 'devHoldingPercent', 'percent'):
            if h.get(k) is not None:
                try:
                    return float(h[k])
                except (TypeError, ValueError):
                    pass
    return None


if __name__ == '__main__':
    import sys
    chain = sys.argv[1] if len(sys.argv) > 1 else CHAIN_BSC
    addr = sys.argv[2] if len(sys.argv) > 2 else '0x3b7a5c1519731e555782bb623adfb9cfca5c7777'
    print(f'合约 {addr} (chain {chain})')
    try:
        print('  top10 集中度:', top10_percent(chain, addr), '%')
    except Web3Error as e:
        print('  top10 失败:', e)
    try:
        print('  dev 持仓:', dev_holding_percent(chain, addr), '%')
    except Web3Error as e:
        print('  dev 失败:', e)
