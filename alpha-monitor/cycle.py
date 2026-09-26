#!/usr/bin/env python3
"""One cloud cycle: legacy daily scan plus hourly/position scan, independently degraded."""
import argparse,json,os,time
from pathlib import Path
import radar,strategy
from cloud import Store,integer

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['scan']);p.add_argument('--data',required=True);p.add_argument('--chain',default='56');p.add_argument('--enrich',type=int,default=12);args=p.parse_args()
    args.safety_checks=True
    args.hourly_enrich=integer(os.environ,'HOURLY_ENRICH_COUNT',120,3,400)
    start=time.time();daily=None;hourly=None;errors=[]
    store=Store(args.data)
    try:
        daily=radar.scan(args)
        if daily['status']!='ok':errors.append('daily_partial')
    except Exception as e:errors.append('daily_'+type(e).__name__)
    try:
        hourly=strategy.scan(args,store,daily)
        if hourly['status']!='ok':errors.append('hourly_partial')
    except Exception as e:errors.append('hourly_'+type(e).__name__)
    try:
        import ranking
        ranking.maybe_emit(store,hourly or {},time.time())
    except Exception as e:errors.append('ranking_'+type(e).__name__)
    try:
        import evaluation
        evaluation.report(store)
    except Exception as e:errors.append('evaluation_'+type(e).__name__)
    status='ok' if not errors else ('failed' if daily is None and hourly is None else 'partial')
    health={'as_of':radar.utc(),'status':status,'errors':errors,'version':strategy.VERSION,'elapsed':round(time.time()-start,1),'daily_status':(daily or {}).get('status'),'hourly_status':(hourly or {}).get('status')}
    radar.dump(Path(args.data)/'health.json',health);print(json.dumps(health),flush=True)
    if status=='failed':raise SystemExit(1)

if __name__=='__main__':main()
