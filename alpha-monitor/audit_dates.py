#!/usr/bin/env python3
"""Compare current Alpha listingTime with earliest available Alpha daily candles."""
import argparse
import concurrent.futures
from pathlib import Path
import radar as r

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',default='data');args=parser.parse_args()
    base=Path(args.data);api=r.PublicAPI(base/'raw',ttl=86400)
    tokens=api.data(r.TOKENS)
    focus=[t for t in tokens if t['chainId']=='56' and t['symbol'].upper() in r.FOCUS]
    def check(t):
        params={'symbol':t['alphaId']+'USDT','interval':'1d','startTime':0,'limit':1500}
        out={'symbol':t['symbol'],'listing_metadata':r.utc(t['listingTime']),'source_url':r.ALPHA+'/klines?'+r.urlencode(params)}
        try:
            b=r.candles(api.data(r.ALPHA+'/klines',params))
            out.update(first_available_day=r.utc(b[0]['t']) if b else None,bars=len(b),predates_listing_metadata=bool(b and b[0]['t']+r.DAY<t['listingTime']))
            if out['predates_listing_metadata']:
                out['first_observed_alpha_next_day_outcome']=r.outcomes(b,b[0]['end'])
        except (RuntimeError,ValueError) as e:out['error']=str(e)
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(check,focus))
    r.dump(base/'listing-date-check.json',results)
    report=base/'research.json'
    if report.exists():r.write_research(base,r.json.loads(report.read_text()))
    print('audited',len(results),'metadata conflicts',sum(x.get('predates_listing_metadata',False) for x in results))

if __name__=='__main__':main()
