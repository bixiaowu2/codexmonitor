import sys,json,urllib.request,urllib.error
sys.path.insert(0,'/opt/alpha-radar')
from cloud import secret,NoRedirect
import os
secret_token=secret(os.environ,'TELEGRAM_BOT_TOKEN')
for method in ('getMe','getWebhookInfo'):
    try:
        req=urllib.request.Request('https://api.telegram.org/bot'+secret_token+'/'+method,data=b'{}',headers={'Content-Type':'application/json'})
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as r: result=json.loads(r.read(65536))
        if not result.get('ok'):print(method+': rejected');continue
        data=result['result']
        if method=='getMe':print(json.dumps({'bot_username':data.get('username'),'is_bot':data.get('is_bot')},ensure_ascii=False))
        else:print(json.dumps({'webhook_enabled':bool(data.get('url')),'pending_update_count':data.get('pending_update_count')}))
    except urllib.error.HTTPError as e:print(method+': HTTP '+str(e.code))
    except Exception:print(method+': network_or_response_error')
