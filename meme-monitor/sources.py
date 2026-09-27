from __future__ import annotations
import math, time
from datetime import datetime
from net import get_json, FetchError

DEX='https://api.dexscreener.com'
GT='https://api.geckoterminal.com/api/v2'
NETWORK={'bsc':'bsc','solana':'solana','robinhood':'robinhood','xlayer':'x-layer','arc':'arc','stable':'stable'}

def address(chain, value):
    value=str(value or '').strip()
    return value if chain=='solana' else value.lower()

def _num(value):
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError): return None

def normalize_pair(pair, chain, source):
    base=pair.get('baseToken') or {}; tx=pair.get('txns') or {}
    p={'chain':chain,'source':source,'address':address(chain,base.get('address')),
       'symbol':base.get('symbol') or '?','name':base.get('name') or '',
       'pair_address':address(chain,pair.get('pairAddress')),'dex':pair.get('dexId') or '',
       'url':pair.get('url') or '', 'created_at':_num(pair.get('pairCreatedAt')),
       'price_usd':_num(pair.get('priceUsd')),'liquidity_usd':_num((pair.get('liquidity') or {}).get('usd')),
       'fdv':_num(pair.get('fdv')),'market_cap':_num(pair.get('marketCap')),'fetched_at':time.time()}
    for short,long in [('m5','5m'),('h1','1h'),('h24','24h')]:
        p['volume_'+long]=_num((pair.get('volume') or {}).get(short))
        p['change_'+long]=_num((pair.get('priceChange') or {}).get(short))
        for side in ('buys','sells'): p[side+'_'+long]=_num((tx.get(short) or {}).get(side))
    return p

def parse_gecko(data, chain):
    if not isinstance(data,dict) or not isinstance(data.get('data'),list): raise FetchError('invalid_schema')
    included={x.get('id'):x.get('attributes',{}) for x in data.get('included',[]) if isinstance(x,dict)}
    out=[]; net=NETWORK[chain]
    for item in data['data'][:20]:
        a=item.get('attributes') or {}; rel=item.get('relationships') or {}
        baseid=((rel.get('base_token') or {}).get('data') or {}).get('id','')
        token=included.get(baseid,{})
        base=baseid.removeprefix(net+'_')
        pool=a.get('address') or str(item.get('id','')).removeprefix(net+'_')
        if not base or not pool: continue
        try: created=datetime.fromisoformat(a.get('pool_created_at','').replace('Z','+00:00')).timestamp()
        except (ValueError,TypeError): created=None
        p=normalize_pair({'baseToken':{'address':base,'symbol':token.get('symbol') or a.get('name','').split('/')[0].strip(),'name':token.get('name')},
            'pairAddress':pool,'dexId':((rel.get('dex') or {}).get('data') or {}).get('id'),
            'pairCreatedAt':created,'priceUsd':a.get('base_token_price_usd'),'liquidity':{'usd':a.get('reserve_in_usd')},
            'volume':a.get('volume_usd'),'priceChange':a.get('price_change_percentage'),'txns':a.get('transactions'),
            'fdv':a.get('fdv_usd'),'marketCap':a.get('market_cap_usd'),
            'url':f'https://www.geckoterminal.com/{net}/pools/{pool}'},chain,'geckoterminal')
        p['symbol_source']='included_token' if token.get('symbol') else 'pool_name'
        out.append(p)
    if data['data'] and not out: raise FetchError('unusable_schema')
    return out

class Collector:
    """Single public provider budget, persistent per-endpoint backoff, no stale candidates."""
    def __init__(self, timeout=8, spacing=3.2):
        self.timeout=timeout; self.spacing=spacing; self.last=0; self.backoff={}; self.failures={}; self.round=0
    def collect(self, chains):
        pairs={}; statuses=[]; self.round+=1
        # Rotate first network so repeated outages cannot systematically starve the last chain.
        chains=list(chains); offset=self.round%len(chains); chains=chains[offset:]+chains[:offset]
        for kind in ('new_pools','trending_pools'):
            for chain in chains:
                key=chain+':'+kind; now=time.time()
                status={'chain':chain,'provider':'geckoterminal','endpoint':kind,'as_of':now,'count':0}
                if self.backoff.get(key,0)>now:
                    status.update(status='backoff',retry_at=self.backoff[key]); statuses.append(status); continue
                time.sleep(max(0,self.spacing-(time.monotonic()-self.last)))
                self.last=time.monotonic()
                try:
                    data=get_json(f'{GT}/networks/{NETWORK[chain]}/{kind}?include=base_token,quote_token,dex',self.timeout)
                    rows=parse_gecko(data,chain)
                    status.update(status='ok' if rows else 'empty',count=len(rows),as_of=time.time())
                    self.failures[key]=0
                    for p in rows:
                        pid=(chain,p['address'],p['pair_address'])
                        # Keep newest successful observation; never merge different pools.
                        p['endpoint']=kind; pairs[pid]=p
                except Exception as e:
                    code=e.code if isinstance(e,FetchError) else type(e).__name__
                    self.failures[key]=self.failures.get(key,0)+1
                    suggested=getattr(e,'retry_after',None)
                    delay=min(900,max(60*2**min(self.failures[key]-1,4),suggested or 0))
                    self.backoff[key]=time.time()+delay
                    status.update(status='failed',error=code,retry_at=self.backoff[key],retry_after=suggested)
                statuses.append(status)
        # Independent discovery source for verified DexScreener chain identifiers.
        dex_chains=[c for c in chains if c in ('bsc','solana','robinhood')]
        profiles=[]; discovery_errors=[]
        for endpoint in ('/token-profiles/latest/v1','/token-boosts/latest/v1') if dex_chains else ():
            key='dex:'+endpoint
            if self.backoff.get(key,0)>time.time():
                discovery_errors.append('backoff');continue
            try:
                data=get_json(DEX+endpoint,self.timeout)
                if not isinstance(data,list):raise FetchError('invalid_schema')
                profiles.extend(data)
            except Exception as e:
                discovery_errors.append(e.code if isinstance(e,FetchError) else type(e).__name__)
                self.backoff[key]=time.time()+max(120,getattr(e,'retry_after',0) or 0)
        for chain in dex_chains:
            status={'chain':chain,'provider':'dexscreener','endpoint':'profile_boost_tokens','as_of':time.time(),'count':0}
            addresses=profile_addresses(profiles,chain)
            key='dex:tokens:'+chain
            try:
                if self.backoff.get(key,0)>time.time():raise FetchError('backoff')
                rows=[]
                if addresses:
                    data=get_json(DEX+'/tokens/v1/'+chain+'/'+','.join(addresses),self.timeout)
                    if not isinstance(data,list):raise FetchError('invalid_schema')
                    for raw in data[:30]:
                        if raw.get('chainId')!=chain:continue
                        p=normalize_pair(raw,chain,'dexscreener')
                        if p['address'] and p['pair_address']:
                            p['discovery']='profile_or_paid_boost_not_quality_endorsement'
                            rows.append(p);pairs.setdefault((chain,p['address'],p['pair_address']),p)
                status.update(status='ok' if rows else 'empty',count=len(rows))
                if discovery_errors:status.update(status='partial',error=','.join(sorted(set(discovery_errors))))
            except Exception as e:
                code=e.code if isinstance(e,FetchError) else type(e).__name__
                if code!='backoff':self.backoff[key]=time.time()+max(120,getattr(e,'retry_after',0) or 0)
                status.update(status='failed',error=code)
            statuses.append(status)
        return list(pairs.values()),statuses


def profile_addresses(profiles,chain,limit=5):
    import re
    out=[]
    for item in profiles:
        if not isinstance(item,dict) or item.get('chainId')!=chain:continue
        value=str(item.get('tokenAddress') or '')
        pattern=r'[1-9A-HJ-NP-Za-km-z]{32,44}' if chain=='solana' else r'0x[0-9a-fA-F]{40}'
        if not re.fullmatch(pattern,value):continue
        value=address(chain,value)
        if value not in out:out.append(value)
        if len(out)>=limit:break
    return out
