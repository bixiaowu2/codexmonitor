import json
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright
import x_monitor as m

accounts=['binancezh','czbinanceprd','heyibinance']
received=[]
class Sink(BaseHTTPRequestHandler):
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        received.append({'channel':self.path,'payload':payload})
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(b'{"ok":true,"result":{"message_id":1}}' if self.path=='/telegram' else b'{"errcode":0}')
    def log_message(self,*args):pass
sink=ThreadingHTTPServer(('127.0.0.1',0),Sink)
threading.Thread(target=sink.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{sink.server_port}'
routes={'*':[m.WebhookTarget('telegram',base+'/telegram',chat_id='local-test'),m.WebhookTarget('dingtalk',base+'/dingtalk')]}
state_path=Path('cloud-live-state.json')
if state_path.exists():
    raise RuntimeError('This validation requires a new state filename; refusing to overwrite previous evidence')
state=m.load_state(state_path)
report={'started_at':datetime.now(timezone.utc).isoformat(),'mode':'normal Chromium under Xvfb','external_messages_sent':False,
        'delivery_targets':'loopback mock sinks, not Telegram or DingTalk real groups','rounds':[]}
def persist():
    report['mock_receiver_requests']=len(received)
    report['pending_count']=len(state['pending'])
    Path('cloud-live-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
try:
    m.notify(m.Tweet('monitor','test','https://x.com','云端本地接收器测试',datetime.now(timezone.utc).isoformat(),'通道测试'),routes)
    report['mock_channel_test_passed']=len(received)==2
    persist()
    with sync_playwright() as p:
        browser=p.chromium.launch_persistent_context('cloud-live-browser-profile',headless=False,viewport={'width':1440,'height':1000})
        try:
            page=browser.pages[0] if browser.pages else browser.new_page()
            for n in range(3):
                started=time.monotonic()
                result={'round':n+1,'started_at':datetime.now(timezone.utc).isoformat(),'accounts':{}}
                for account in accounts:
                    try:
                        posts=m.scrape_account(page,account,max_items=20)
                        m.ingest(state,account,posts,routes,state_path)
                        m.record_scrape_health(state,account,True,routes,state_path)
                        m.deliver_pending(state,routes,state_path)
                        newest=max(posts,key=lambda t:int(t.tweet_id))
                        result['accounts'][account]={'success':True,'posts':len(posts),'latest':asdict(newest)}
                    except Exception as exc:
                        result['accounts'][account]={'success':False,'error':str(exc)[:300]}
                    print(json.dumps({'round':n+1,'account':account,'result':result['accounts'][account]},ensure_ascii=False),flush=True)
                result['seconds']=round(time.monotonic()-started,2)
                report['rounds'].append(result);persist()
                if n<2:
                    time.sleep(max(0,120-(time.monotonic()-started)))
        finally:browser.close()
finally:
    sink.shutdown();sink.server_close()
    report['completed_at']=datetime.now(timezone.utc).isoformat()
    report['initialized_accounts']=state['initialized_accounts']
    persist()
print('LIVE_PROBE_FINISHED',flush=True)
