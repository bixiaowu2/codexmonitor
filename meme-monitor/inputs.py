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
        p['x_account_count']=len({e.get('account') for e in xx if e.get('account')})
        p.update(smart_wallet_hits=len(ws),smart_wallet_sources=list(ws.values())[:5],kol_hits=len(ks),x_hits=len(xx),mention_sources=(ks+xx)[:5])
    return pairs


def shared_x_mentions(path, pairs, now=None):
    """Read only original public posts. EVM attribution requires explicit chain context."""
    import sqlite3,re
    from urllib.parse import urlsplit
    now=time.time() if now is None else now
    if path is None:return [],{'status':'disabled'}
    try:
        with __import__('contextlib').closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
            registry_row=db.execute("SELECT payload FROM metadata WHERE key='accounts'").fetchone()
            registry=json.loads(registry_row[0]) if registry_row else {}
            health={r[0]:{'last_success':r[1],'failures':r[2]} for r in db.execute('SELECT account,last_success,failures FROM accounts')}
            posts=db.execute('SELECT id,account,url,text,published,observed FROM posts WHERE published BETWEEN ? AND ? ORDER BY published DESC LIMIT 2000',(now-86400,now)).fetchall()
    except (OSError,ValueError,sqlite3.Error):return [],{'status':'unavailable'}
    current={h for h in registry if 0<=now-health.get(h,{}).get('last_success',0)<=max(300,registry[h].get('interval_seconds',120)*2+60)}
    events=[];index={(p['chain'],p['address']) for p in pairs}
    chain_patterns={
        'bsc':r'\bBSC\b|\bBNB\s*Chain\b|bscscan\.com',
        'robinhood':r'\bRobinhood(?:\s+Chain)?\b',
        'xlayer':r'\bX\s*Layer\b|oklink\.com/xlayer',
        'arc':r'\bon\s+Arc\b|\bArc\s+(?:Chain|Network)\b|Arc链|arcscan',
        'stable':r'\bon\s+Stable\b|\bStable\s+(?:Chain|Network)\b|Stable链|stablescan',
        'solana':r'\bSolana\b|solscan\.io'}
    for post_id,account,url,text,published,observed in posts:
        if account not in registry:continue
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc not in ('x.com','twitter.com') or parsed.path.lower()!=f'/{account}/status/{post_id}'.lower():continue
        hints={c for c,pattern in chain_patterns.items() if re.search(pattern,text,re.I)}
        evm={a.lower() for a in re.findall(r'(?<![A-Za-z0-9])0x[0-9a-fA-F]{40}(?![A-Za-z0-9])',text)}
        sol=set(re.findall(r'(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])',text))
        for chain,token in index:
            matched=(chain=='solana' and token in sol) or (chain!='solana' and hints=={chain} and token.lower() in evm)
            if matched:events.append({'chain':chain,'address':token,'account':account,'url':url,'source':'x-monitor','observed_at':published,'collected_at':observed,'category':registry[account]['category'],'excerpt':text[:100],'stance':'unclassified'})
    status='ok' if registry and len(current)==len(registry) else 'partial'
    return events,{'status':status,'accounts':len(registry),'fresh_accounts':len(current),'posts_24h':len(posts),'matched_events':len(events),'as_of':now}
