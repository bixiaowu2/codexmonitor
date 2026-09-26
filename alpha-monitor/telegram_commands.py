"""Private-chat command ingestion; offset, reply and journal mutation commit together."""
import json,time,urllib.request
from positions import initialize,handle_command,emit

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def process_updates(store,config,updates,now=None):
    now=time.time() if now is None else now
    with store.db() as db:initialize(db)
    for update in sorted(updates,key=lambda x:x.get('update_id',-1)):
        uid=update.get('update_id')
        if not isinstance(uid,int):continue
        with store.db() as db:
            row=db.execute("SELECT value FROM runtime WHERE key='telegram_offset'").fetchone()
            offset=json.loads(row[0]) if row else 0
            if uid<offset:continue
            m=update.get('message',{});chat=m.get('chat',{});sender=m.get('from',{})
            allowed=(str(chat.get('id'))==config.chat and chat.get('type')=='private' and sender.get('id')==chat.get('id') and not sender.get('is_bot') and not m.get('forward_origin'))
            text=m.get('text','');stamp=m.get('date',0)
            if allowed and isinstance(text,str) and text.startswith('/') and -60<=now-stamp<=600:
                reply=handle_command(db,text,store.folder,'telegram:'+str(uid),now)
                for i in range(0,len(reply),1500):emit(db,f'command:{uid}:{i}',reply[i:i+1500],now)
            db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',('telegram_offset',json.dumps(uid+1)))

def poll(store,config):
    if not config.enabled:return
    body=json.dumps({'offset':store.state('telegram_offset',0),'limit':20,'timeout':0,'allowed_updates':['message']}).encode()
    try:
        req=urllib.request.Request('https://api.telegram.org/bot'+config.token+'/getUpdates',data=body,headers={'Content-Type':'application/json'})
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as r:data=json.loads(r.read(1000000))
        if not data.get('ok') or not isinstance(data.get('result'),list):raise ValueError()
    except Exception:
        store.put('commands',{'status':'retry','checked':time.time(),'error':'telegram_command_poll_failed'});return
    process_updates(store,config,data['result'])
    store.put('commands',{'status':'ok','checked':time.time()})
