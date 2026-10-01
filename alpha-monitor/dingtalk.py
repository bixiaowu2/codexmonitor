"""Independent DingTalk alert outbox; chat commands stay private to Telegram."""
import base64,hashlib,hmac,json,time,urllib.error,urllib.parse,urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class DingError(Exception):pass

def valid_webhook(value):
    try:
        p=urllib.parse.urlsplit(value);q=urllib.parse.parse_qs(p.query)
        return p.scheme=='https' and p.hostname=='oapi.dingtalk.com' and p.path=='/robot/send' and p.port in (None,443) and not p.username and bool(q.get('access_token',[''])[0]) and not p.fragment
    except ValueError:return False

def initialize(store):
    with store.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS dingtalk_outbox(
          id INTEGER PRIMARY KEY,key TEXT UNIQUE NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
          next_try REAL NOT NULL DEFAULT 0,sent REAL,error TEXT);
        CREATE INDEX IF NOT EXISTS dingtalk_pending ON dingtalk_outbox(id) WHERE state='pending';
        CREATE TRIGGER IF NOT EXISTS dingtalk_fanout AFTER INSERT ON outbox
          WHEN NEW.key NOT LIKE 'command:%' BEGIN
          INSERT OR IGNORE INTO dingtalk_outbox(key,created,payload) VALUES(NEW.key,NEW.created,NEW.payload);
        END;
        ''')

def signed_url(webhook,secret,now=None):
    if not secret:return webhook
    ts=str(int((time.time() if now is None else now)*1000))
    signature=base64.b64encode(hmac.new(secret.encode(),(ts+'\n'+secret).encode(),hashlib.sha256).digest()).decode()
    p=urllib.parse.urlsplit(webhook)
    q=[(k,v) for k,v in urllib.parse.parse_qsl(p.query) if k not in ('timestamp','sign')]
    q.extend([('timestamp',ts),('sign',signature)])
    return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,urllib.parse.urlencode(q),''))

def send(config,text):
    if not valid_webhook(config.ding_webhook):raise DingError('dingtalk_invalid_webhook')
    # Consistent keyword supports robots configured with keyword Alpha雷达.
    content=(config.ding_keyword+' · Alpha雷达\n'+text).encode('utf-8')[:15000].decode('utf-8',errors='ignore')
    body=json.dumps({'msgtype':'text','text':{'content':content},'at':{'isAtAll':False}}).encode()
    req=urllib.request.Request(signed_url(config.ding_webhook,config.ding_secret),data=body,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as r:data=json.loads(r.read(65536))
    except urllib.error.HTTPError as e:raise DingError('dingtalk_http_'+str(e.code)) from None
    except Exception:raise DingError('dingtalk_network_or_response_error') from None
    if not isinstance(data,dict) or data.get('errcode')!=0:raise DingError('dingtalk_rejected')

def deliver_one(store,config,send_fn=send,now=None):
    from cloud import message
    now=time.time() if now is None else now
    with store.db() as db:
        db.execute("UPDATE dingtalk_outbox SET state='expired',error='signal_expired' WHERE state='pending' AND created<?",(now-config.ttl,))
        row=db.execute("SELECT * FROM dingtalk_outbox WHERE state='pending' AND next_try<=? ORDER BY id LIMIT 1",(now,)).fetchone()
    if not config.ding_enabled or not row:return False
    payload=json.loads(row['payload'])
    if not config.notify_new and payload.get('event')=='新发现交集（不等于刚上市）':
        store.write("UPDATE dingtalk_outbox SET state='skipped' WHERE id=?",(row['id'],))
        return True
    try:send_fn(config,message(row))
    except DingError as e:
        delay=min(3600,60*2**min(row['attempts'],6))
        store.write('UPDATE dingtalk_outbox SET attempts=attempts+1,next_try=?,error=? WHERE id=?',(now+delay,str(e),row['id']))
        store.put('dingtalk',{'status':'retry','checked':now,'error':str(e)})
    else:
        store.write("UPDATE dingtalk_outbox SET state='sent',sent=?,attempts=attempts+1,error=NULL WHERE id=?",(now,row['id']))
        store.put('dingtalk',{'status':'ok','checked':now})
    return True
