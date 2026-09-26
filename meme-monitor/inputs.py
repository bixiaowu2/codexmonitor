"""Optional audited event files. These are not live smart-wallet or X integrations."""
import json,time
from pathlib import Path
from sources import address

def read_json(path,default):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return default

def valid_event(e,now):
    try: age=now-float(e.get('observed_at',0))
    except (ValueError,TypeError):return False
    return isinstance(e,dict) and 0<=age<=86400 and e.get('chain') and str(e.get('url') or e.get('source') or '').startswith('https://')

def wallet_index(path):
    raw=read_json(path,[])
    if isinstance(raw,dict):raw=raw.get('wallets',[])
    return {str(i):x for i,x in enumerate(raw if isinstance(raw,list) else []) if isinstance(x,dict) and valid_event(x,time.time()) and x.get('address') and x.get('token')}

def kol_events(path,now=None):
    now=time.time() if now is None else now; out=[];seen=set()
    try:lines=Path(path).read_text().splitlines()[-1000:]
    except OSError:return out
    for line in lines:
        try:e=json.loads(line)
        except ValueError:continue
        if not isinstance(e,dict) or not valid_event(e,now) or not e.get('address'):continue
        key=(e['chain'],address(e['chain'],e['address']),e.get('url') or e.get('source'))
        if key not in seen:out.append(e);seen.add(key)
    return out
x_mentions=kol_events

def enrich(pairs,wallets,kols,xs):
    for p in pairs:
        def matches(e,field='address'):
            return e.get('chain')==p['chain'] and address(p['chain'],e.get(field))==p['address']
        ws={address(p['chain'],w['address']):w for w in wallets.values() if matches(w,'token')}
        ks=[e for e in kols if matches(e)]; xx=[e for e in xs if matches(e)]
        p.update(smart_wallet_hits=len(ws),smart_wallet_sources=list(ws.values())[:5],kol_hits=len(ks),x_hits=len(xx),mention_sources=(ks+xx)[:5])
    return pairs
