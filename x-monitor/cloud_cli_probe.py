import json, os, subprocess, threading, time, hashlib
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from datetime import datetime,timezone
calls=[]
class Sink(BaseHTTPRequestHandler):
 def do_POST(self):
  self.rfile.read(int(self.headers['Content-Length'])); calls.append(self.path)
  self.send_response(200);self.end_headers()
  self.wfile.write(b'{"ok":true}' if self.path=='/telegram' else b'{"errcode":0}')
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Sink)
threading.Thread(target=server.serve_forever,daemon=True).start()
root=Path.cwd();routes=root/'cloud-cli-routes.json';state=root/'cloud-cli-state.json'
if state.exists():raise RuntimeError('previous CLI probe state exists; refusing overwrite')
routes.write_text(json.dumps({'default':[{'type':'telegram','url':f'http://127.0.0.1:{server.server_port}/telegram','chat_id':'mock-only'}, {'type':'dingtalk','url':f'http://127.0.0.1:{server.server_port}/dingtalk'}]}))
env=os.environ.copy();env.update({'X_ACCOUNTS':'binancezh,czbinanceprd,heyibinance','HEADLESS':'false',
 'PYTHON_BIN':str(root.parent/'.venv/bin/python'),'ROUTES_FILE':str(routes),'STATE_FILE':str(state),'BROWSER_PROFILE_DIR':str(root/'cloud-cli-browser-profile'),
 'TELEGRAM_BOT_TOKEN':'','TELEGRAM_CHAT_ID':'','DINGTALK_WEBHOOK':'','WECOM_WEBHOOK':''})
results=[]
try:
 for action in ['--test-webhooks','--once','--once']:
  start=time.monotonic()
  r=subprocess.run(['bash','scripts/run-monitor.sh',action],env=env,text=True,capture_output=True,timeout=180)
  entry={'action':action,'exit_code':r.returncode,'seconds':round(time.monotonic()-start,2),'log':r.stderr[-3000:]}
  if state.exists():
   saved=json.loads(state.read_text());entry.update({'seen':{a:len(v) for a,v in saved['seen'].items()},'initialized_accounts':saved['initialized_accounts'],'pending_count':len(saved['pending'])})
  results.append(entry);print(json.dumps(entry,ensure_ascii=False),flush=True)
finally:
 server.shutdown();server.server_close()
 report={'completed_at':datetime.now(timezone.utc).isoformat(),'source_sha256':hashlib.sha256(Path('x_monitor.py').read_bytes()).hexdigest(),
         'external_messages_sent':False,'mock_channel_requests':calls,'commands':results}
 Path('cloud-cli-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
