import json,os,time
from pathlib import Path
try:
    data=json.loads((Path(os.environ.get('RADAR_DATA','/data'))/'cloud-health.json').read_text())
    assert 0<=time.time()-data['heartbeat']<90
    # A fresh heartbeat alone must not hide a persistently broken scanner/notifier.
    assert data.get('scan',{}).get('failures',0)<3
    assert data.get('fast',{}).get('failures',0)<3
    telegram=data.get('telegram',{})
    assert telegram.get('status')!='retry'
    assert data.get('dingtalk',{}).get('status')!='retry'
    assert data.get('commands',{}).get('status')!='retry'
except Exception:raise SystemExit(1)
