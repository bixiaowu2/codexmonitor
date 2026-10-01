#!/usr/bin/env python3
"""Shared support worker for the existing three investment radars, not an order executor."""
import argparse,base64,concurrent.futures,fcntl,hashlib,hmac,json,os,sqlite3,time
from pathlib import Path
import urllib.request,urllib.error,urllib.parse
import holdings_core as core
from holdings_sources import alpha_positions,fetch_position

DATA=Path('/var/lib/radar-holdings')
ALPHA_DB=Path('/var/lib/alpha-radar/radar.sqlite')
ALPHA_REPORT=ALPHA_DB.parent/'position-trends.json'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def request(url,body):
    req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=7) as response:return json.loads(response.read(2000000))
    except urllib.error.HTTPError as e:raise RuntimeError('http_'+str(e.code)) from None
    except Exception:raise RuntimeError('network_or_response_error') from None

def secret(env,name):
    return Path(env[name+'_FILE']).read_text().strip() if env.get(name+'_FILE') else env.get(name,'').strip()

def routes(env=None):
    env=os.environ if env is None else env;result={}
    for domain,prefix in [('alpha',''),('meme','MEME_'),('stock','STOCK_')]:
        r={key:secret(env,prefix+name) for key,name in [('token','TELEGRAM_BOT_TOKEN'),('chat','TELEGRAM_CHAT_ID'),('webhook','DINGTALK_WEBHOOK'),('signing','DINGTALK_SECRET')]}
        r['telegram']=env.get(prefix+'TELEGRAM_ENABLED','true' if domain=='alpha' else 'false').lower()=='true'
        ding=env.get(prefix+'DINGTALK_ENABLED','auto' if domain=='alpha' else 'false').lower()
        r['dingtalk']=bool(r['webhook']) if ding=='auto' else ding=='true'
        r['keyword']=env.get(prefix+'DINGTALK_KEYWORD',{'alpha':'DT','meme':'meme','stock':'stock'}[domain])
        if r['telegram'] and (not r['token'] or not r['chat']):raise ValueError('missing_'+domain+'_telegram_configuration')
        if r['dingtalk']:
            u=urllib.parse.urlsplit(r['webhook'])
            if u.scheme!='https' or u.hostname!='oapi.dingtalk.com' or u.path!='/robot/send' or u.username or u.port not in (None,443):raise ValueError('invalid_'+domain+'_dingtalk_configuration')
        result[domain]=r
    return result

def state(db,key,default=None):
    row=db.execute('SELECT value FROM runtime WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default

def put(db,key,value):db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))

def chunks(text,limit=3400):
    result=[];current='';size=0
    for ch in text:
        units=len(ch.encode('utf-16-le'))//2
        if size+units>limit:result.append(current);current='';size=0
        current+=ch;size+=units
    if current:result.append(current)
    return result

def enqueue(db,key,domain,route,text,now,private=False):
    for channel in ('telegram','dingtalk'):
        if not route.get(channel) or (private and channel!='telegram'):continue
        for index,part in enumerate(chunks(text)):
            db.execute('INSERT OR IGNORE INTO outbox(key,domain,channel,created,expires,text) VALUES(?,?,?,?,?,?)',
                       (f'{key}:{channel}:{index}',domain,channel,now,now+(3600 if private else 900),part))

def poll(db,domain,route,now,request_fn=request):
    if not route['telegram']:return {'status':'disabled'}
    offset=state(db,'offset:'+domain,0)
    result=request_fn('https://api.telegram.org/bot'+route['token']+'/getUpdates',{'offset':offset,'timeout':0,'limit':30,'allowed_updates':['message']})
    if not isinstance(result,dict) or result.get('ok') is not True:raise RuntimeError('telegram_updates_rejected')
    updates=result.get('result')
    if not isinstance(updates,list):raise RuntimeError('telegram_updates_schema')
    accepted=0
    for update in updates:
        uid=update.get('update_id')
        if not isinstance(uid,int) or uid<offset:continue
        with db:
            if core.authorized_update(update,route['chat'],now):
                key=f'command:{domain}:{uid}'
                reply=core.command(db,domain,update['message']['text'],key,now)
                enqueue(db,key,domain,route,reply,now,private=True);accepted+=1
            offset=max(offset,uid+1);put(db,'offset:'+domain,offset)
    return {'status':'ok','accepted':accepted,'checked':now}

def send(route,text,channel,request_fn=request):
    if channel=='telegram':
        result=request_fn('https://api.telegram.org/bot'+route['token']+'/sendMessage',{'chat_id':route['chat'],'text':text,'disable_web_page_preview':True})
        if not isinstance(result,dict) or result.get('ok') is not True:raise RuntimeError('telegram_send_rejected')
    elif channel=='dingtalk':
        url=route['webhook']
        if route['signing']:
            stamp=str(int(time.time()*1000));signature=base64.b64encode(hmac.new(route['signing'].encode(),(stamp+'\n'+route['signing']).encode(),hashlib.sha256).digest()).decode()
            url+='&'+urllib.parse.urlencode({'timestamp':stamp,'sign':signature})
        result=request_fn(url,{'msgtype':'text','text':{'content':route['keyword']+' · 持仓走势\n'+text},'at':{'isAtAll':False}})
        if not isinstance(result,dict) or result.get('errcode')!=0:raise RuntimeError('dingtalk_send_rejected')
    else:raise RuntimeError('invalid_channel')

def deliver(db,configured,now,send_fn=send):
    with db:db.execute("UPDATE outbox SET state='expired' WHERE state='pending' AND expires<?",(now,))
    rows=db.execute("SELECT * FROM outbox WHERE state='pending' AND next_try<=? ORDER BY created LIMIT 12",(now,)).fetchall()
    for row in rows:
        try:send_fn(configured[row['domain']],row['text'],row['channel'])
        except Exception:
            with db:db.execute('UPDATE outbox SET attempts=attempts+1,next_try=?,error=? WHERE key=?',(time.time()+min(900,30*2**min(row['attempts'],5)),'delivery_failed',row['key']))
        else:
            with db:db.execute("UPDATE outbox SET state='sent',attempts=attempts+1,sent=?,error=NULL WHERE key=?",(time.time(),row['key']))

def atomic(path,value,owner=None):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False));os.chmod(tmp,0o600)
    if owner:os.chown(tmp,*owner)
    tmp.replace(path)

def review_one(position):
    try:
        quote,bars,frame,safety=fetch_position(position)
        return core.analyze(position,quote,bars,time.time(),frame,safety)
    except Exception as exc:
        # Provider exceptions must never carry credential-bearing URLs into reports.
        result=core.analyze(position,None,[],time.time(),'1d' if position['domain']=='stock' else '1h')
        result['error']=str(exc) if isinstance(exc,ValueError) and (str(exc) in ('market_http_unavailable','no_verified_base_token_pool','no_recent_traded_meme_bar','no_closed_stock_bar','stock_currency_mismatch','stock_symbol_mismatch','alpha_identity_or_schema') or str(exc) in ['market_http_'+str(code) for code in (400,401,403,404,429,500,502,503,504)]) else type(exc).__name__
        result['text']+='\n本轮数据核验未完成：'+result['error']
        return result

def cycle(db,configured,now=None,review_fn=review_one,alpha_path=ALPHA_DB,alpha_report=ALPHA_REPORT):
    now=time.time() if now is None else now;health={'version':core.VERSION,'as_of':now,'commands':{},'errors':[]}
    for domain in ('meme','stock'):
        try:health['commands'][domain]=poll(db,domain,configured[domain],now)
        except Exception as e:health['commands'][domain]={'status':'failed','error':str(e) if isinstance(e,RuntimeError) else type(e).__name__}
    try:alpha=alpha_positions(alpha_path)
    except Exception:alpha=[];health['errors'].append('alpha_journal_unavailable')
    positions=alpha+[dict(p) for p in db.execute('SELECT * FROM positions WHERE closed IS NULL')]
    def key(p):return p['domain']+':'+str(p['id'])
    old={r['key']:dict(r) for r in db.execute('SELECT * FROM reviews')}
    due=sorted([p for p in positions if now-old.get(key(p),{}).get('checked',0)>=300],key=lambda p:old.get(key(p),{}).get('checked',0))[:6]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(review_fn,due))
    # A simultaneous Alpha /buy may have closed/replaced a journal row while HTTP ran.
    try:current_alpha={p['id']:p for p in alpha_positions(alpha_path)}
    except Exception:current_alpha={}
    for p,result in zip(due,results):
        if p['domain']=='alpha' and (p['id'] not in current_alpha or current_alpha[p['id']]['created']!=p['created']):continue
        k=key(p)
        with db:
            if p['domain']!='alpha' and result['fresh']:db.execute('UPDATE positions SET peak=? WHERE id=?',(result['peak'],p['id']))
            db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?,?)',(k,time.time(),result.get('quote_time'),json.dumps(result,ensure_ascii=False)))
            if result['fresh']:
                # Alpha already owns fixed/trailing-stop alerts; this worker adds trend context.
                risk_flags=[f for f in result['flags'] if p['domain']!='alpha' or f=='跌破此前20根K线低点参考']
                before=state(db,'risk:'+k,[])
                if any(flag not in before for flag in risk_flags):
                    enqueue(db,f'risk:{k}:{int(now)}',p['domain'],configured[p['domain']],'🔔 持仓规则触发\n'+result['text'],now)
                put(db,'risk:'+k,risk_flags)
    reports={r['key']:json.loads(r['payload']) for r in db.execute('SELECT * FROM reviews')}
    for domain in ('alpha','meme','stock'):
        rows=[reports[key(p)] for p in positions if p['domain']==domain and key(p) in reports]
        fresh=[r for r in rows if r['fresh'] and r.get('quote_time') is not None and time.time()-r['quote_time']<= (900 if domain=='stock' else 600 if domain=='meme' else 180)]
        bucket=int(now//3600)
        if fresh and state(db,'summary:'+domain)!=bucket:
            with db:
                enqueue(db,f'summary:{domain}:{bucket}',domain,configured[domain],'📊 已登记实际持仓走势（小时汇总）\n\n'+'\n\n'.join(r['text'] for r in fresh),now)
                put(db,'summary:'+domain,bucket)
        if domain=='alpha' and Path(alpha_path).exists():
            try:
                info=Path(alpha_path).stat();atomic(Path(alpha_report),{'as_of':time.time(),'rows':rows},(info.st_uid,info.st_gid))
            except Exception:health['errors'].append('alpha_trend_export_failed')
    health.update(positions=len(positions),checked=len(due),fresh=sum(r['fresh'] for r in results),stale_or_missing=sum(not r['fresh'] for r in results))
    health['delivery']=[dict(r) for r in db.execute('SELECT domain,channel,state,count(*) AS count FROM outbox GROUP BY domain,channel,state')]
    with db:put(db,'health',health)
    return health

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');parser.add_argument('--announce',action='store_true');args=parser.parse_args()
    configured=routes();DATA.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(DATA,0o700)
    with (DATA/'worker.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        db=sqlite3.connect(DATA/'holdings.sqlite',timeout=10);core.initialize(db)
        if args.check:
            for domain,r in configured.items():
                if not r['telegram']:continue
                result=request('https://api.telegram.org/bot'+r['token']+'/getMe',{})
                if result.get('ok') is not True:raise RuntimeError('bot_check_failed')
                chat=request('https://api.telegram.org/bot'+r['token']+'/getChat',{'chat_id':r['chat']})
                private=chat.get('ok') is True and chat.get('result',{}).get('type')=='private'
                if not private:raise RuntimeError('registration_requires_private_chat_'+domain)
                with db:put(db,'bot:'+domain,result['result'].get('username'))
                print(json.dumps({'domain':domain,'bot':result['result'].get('username'),'authenticated':True,'private_chat':private}))
        else:
            if args.announce:
                with db:
                    for domain in ('meme','stock'):enqueue(db,'holdings-guide-v1:'+domain,domain,configured[domain],core.help_text(domain),time.time(),private=True)
            health=cycle(db,configured)
            from alpha_hypothesis import cycle as hypothesis_cycle
            try:
                hypothesis=hypothesis_cycle(db,time.time())
                health['alpha_hypothesis']={k:hypothesis.get(k) for k in ('version','status','checked')}
            except Exception as exc:health['errors'].append('alpha_hypothesis_'+type(exc).__name__)
            from daily_brief import queue_daily
            try:health['daily_brief']=queue_daily(db,configured,time.time(),enqueue)
            except Exception as exc:health['errors'].append('daily_brief_'+type(exc).__name__)
            deliver(db,configured,time.time())
            health['delivery']=[dict(r) for r in db.execute('SELECT domain,channel,state,count(*) AS count FROM outbox GROUP BY domain,channel,state')]
            with db:put(db,'health',health)
            atomic(DATA/'health.json',health)
            with db:db.execute("DELETE FROM outbox WHERE state<>'pending' AND created<?",(time.time()-30*86400,))
            print(json.dumps({k:health[k] for k in ('version','as_of','positions','checked','fresh','stale_or_missing','errors')}))
        db.close();os.chmod(DATA/'holdings.sqlite',0o600)

if __name__=='__main__':main()
