"""Public, read-only market data adapters for actual holdings."""
import json,sqlite3,subprocess,time
from pathlib import Path
from urllib.parse import urlencode,quote
from holdings_core import number

ALPHA='https://www.binance.com/bapi/defi/v1/public/alpha-trade'
GT='https://api.geckoterminal.com/api/v2'
NETWORK={'bsc':'bsc','solana':'solana','robinhood':'robinhood','xlayer':'x-layer','arc':'arc','stable':'stable'}

def public_json(url,params=None):
    full=url+('?' + urlencode(params) if params else '')
    r=subprocess.run(['curl','--user-agent','radar-holdings/1.0','--header','Accept: application/json','--compressed','--silent','--show-error','--fail','--connect-timeout','3','--max-time','7','--write-out','\n%{http_code}',full],capture_output=True,timeout=9)
    body,_,status=r.stdout.rpartition(b'\n')
    if r.returncode:raise ValueError('market_http_'+status.decode() if status.isdigit() and status!=b'000' else 'market_http_unavailable')
    return json.loads(body)

def alpha_positions(path):
    if not Path(path).exists():return []
    db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        return [dict(r,domain='alpha',market='bsc',instrument=r['address']) for r in db.execute("SELECT * FROM positions WHERE closed IS NULL AND mode='actual'")]
    finally:db.close()

def bars_from_alpha(raw,now,seconds):
    rows=raw.get('data',[]) if isinstance(raw,dict) else raw
    out=[]
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,list) or len(row)<9:continue
        values=[number(row[i]) for i in (0,1,2,3,4,5,6,8)]
        if any(v is None for v in values):continue
        t,o,h,l,c,v,end,trades=values
        if v<=0 or trades<1 or end>=now*1000 or end-t!=seconds*1000-1:continue
        out.append({'time':t/1000,'end':(end+1)/1000,'open':o,'high':h,'low':l,'close':c})
    return out

def alpha_data(p,get=public_json):
    symbol=p['alpha_id']+'USDT';raw=get(ALPHA+'/ticker',{'symbol':symbol});raw=raw.get('data',raw)
    if not isinstance(raw,dict) or raw.get('symbol')!=symbol:raise ValueError('alpha_identity_or_schema')
    price,stamp=number(raw.get('lastPrice')),number(raw.get('closeTime'))
    q={'price':price,'time':stamp/1000 if stamp else None,'source':'Binance Alpha成交','currency':'USDT'}
    if price is None or stamp is None or not 0<=time.time()-stamp/1000<=180:
        try:
            minutes=bars_from_alpha(get(ALPHA+'/klines',{'symbol':symbol,'interval':'1m','limit':4}),time.time(),60)
            recent=[b for b in minutes if 0<=time.time()-b['time']<=180]
            if recent:
                b=max(recent,key=lambda b:b['time']);q.update(price=b['close'],time=b['time'],source='Binance Alpha有成交分钟K线，时间取保守下界')
        except Exception:pass
    try:bars=bars_from_alpha(get(ALPHA+'/klines',{'symbol':symbol,'interval':'1h','limit':80}),time.time(),3600)
    except Exception:bars=[]
    return q,bars,'1h','持仓集中、关联钱包与卖出执行仍需独立核验'

def chart_rows(raw,symbol,seconds,now):
    result=raw['chart']['result'][0];meta=result['meta']
    if str(meta.get('symbol','')).upper()!=symbol.upper():raise ValueError('stock_symbol_mismatch')
    indicators=result['indicators']['quote'][0];out=[]
    regular=(meta.get('currentTradingPeriod') or {}).get('regular',{})
    for i,stamp in enumerate(result.get('timestamp') or []):
        values=[number(stamp)]+[number((indicators.get(k) or [])[i]) if i<len(indicators.get(k) or []) else None for k in ('open','high','low','close','volume')]
        if any(v is None for v in values):continue
        t,o,h,l,c,volume=values
        if volume<=0:continue
        # Yahoo daily timestamps start at session open; avoid treating today's unfinished bar as closed.
        end=t+seconds
        if seconds==86400:
            if number(regular.get('start')) is not None and t>=regular['start'] and now<regular.get('end',float('inf')):continue
            end=regular['end'] if number(regular.get('start')) is not None and t>=regular['start'] and number(regular.get('end')) is not None else t+8*3600
        if end>now or min(o,h,l,c)<=0 or not l<=min(o,c)<=max(o,c)<=h:continue
        out.append({'time':t,'end':end,'open':o,'high':h,'low':l,'close':c})
    return sorted(out,key=lambda b:b['time']),meta

def stock_data(p,get=public_json):
    symbol=p['instrument'];url='https://query1.finance.yahoo.com/v8/finance/chart/'+quote(symbol,safe='')
    recent,meta=chart_rows(get(url,{'interval':'5m','range':'5d','includePrePost':'false'}),symbol,300,time.time())
    expected={'a':'CNY','hk':'HKD','us':'USD'}[p['market']]
    if meta.get('currency')!=expected:raise ValueError('stock_currency_mismatch')
    if not recent:raise ValueError('no_closed_stock_bar')
    last=recent[-1];q={'price':last['close'],'time':last['end'],'source':'Yahoo已收盘5分钟K线','currency':expected}
    try:bars,_=chart_rows(get(url,{'interval':'1d','range':'6mo','includePrePost':'false'}),symbol,86400,time.time())
    except Exception:bars=[]
    return q,bars,'1d','停牌、跳空、涨跌停和交易时段可能限制成交'

def meme_data(p,get=public_json):
    net=NETWORK[p['market']];address=p['instrument']
    data=get(GT+'/networks/'+net+'/tokens/'+quote(address,safe='')+'/pools',{'page':1})
    pools=[]
    for item in data.get('data',[]):
        base=((item.get('relationships') or {}).get('base_token') or {}).get('data',{}).get('id','')
        expected=net+'_'+address
        if (base if p['market']=='solana' else base.lower())!=(expected if p['market']=='solana' else expected.lower()):continue
        a=item.get('attributes') or {};reserve=number(a.get('reserve_in_usd'))
        if reserve is not None and reserve>0 and a.get('address'):pools.append((reserve,a))
    if not pools:raise ValueError('no_verified_base_token_pool')
    reserve,pool=max(pools,key=lambda x:x[0]);pool_id=pool['address']
    data=get(GT+'/networks/'+net+'/pools/'+quote(pool_id,safe='')+'/ohlcv/minute',{'aggregate':5,'limit':80,'currency':'usd','token':'base'})
    rows=data.get('data',{}).get('attributes',{}).get('ohlcv_list',[]);bars=[];now=time.time()
    for row in rows:
        if not isinstance(row,list) or len(row)<6:continue
        values=[number(v) for v in row[:6]]
        if any(v is None for v in values):continue
        t,o,h,l,c,volume=values
        if volume<=0 or t+300>now or min(o,h,l,c)<=0 or not l<=min(o,c)<=max(o,c)<=h:continue
        bars.append({'time':t,'end':t+300,'open':o,'high':h,'low':l,'close':c})
    bars.sort(key=lambda b:b['time'])
    if not bars:raise ValueError('no_recent_traded_meme_bar')
    last=bars[-1];q={'price':last['close'],'time':last['time'],'source':'GeckoTerminal有成交5分钟K线，时间取保守下界','currency':'USD'}
    return q,bars,'5m',f'该池报告流动性 {reserve:,.2f} USD；可卖性/税费/集中度未在本分析独立核验'

def fetch_position(p):
    return {'alpha':alpha_data,'stock':stock_data,'meme':meme_data}[p['domain']](p)
