#!/usr/bin/env python3
"""Single-instance supervisor + durable Telegram outbox. No trading credentials."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parent

def integer(env,name,default,low,high):
    try:value=int(env.get(name,str(default)))
    except ValueError:raise ValueError(name+' must be an integer') from None
    if not low<=value<=high:raise ValueError(f'{name} must be {low}..{high}')
    return value

def secret(env,name):
    path=env.get(name+'_FILE')
    return Path(path).read_text().strip() if path else env.get(name,'').strip()

@dataclass(frozen=True)
class Config:
    data: Path
    token: str
    chat: str
    chain: str
    interval: int
    enrich: int
    timeout: int
    ttl: int
    retention: int
    notify_new: bool
    enabled: bool
    ding_enabled: bool=False
    ding_webhook: str=""
    ding_secret: str=""
    ding_keyword: str="Alpha雷达"
    fast_interval: int=60
    fast_timeout: int=150
    @classmethod
    def load(cls,env=None):
        e=os.environ if env is None else env
        token=secret(e,'TELEGRAM_BOT_TOKEN');chat=secret(e,'TELEGRAM_CHAT_ID')
        enabled=e.get('TELEGRAM_ENABLED','true').strip().lower()
        if enabled not in ('true','false'):raise ValueError('TELEGRAM_ENABLED must be true or false')
        enabled=enabled=='true'
        if enabled and not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token):raise ValueError('Configure TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN_FILE')
        if enabled and not re.fullmatch(r'-?[0-9]+',chat):raise ValueError('Configure numeric TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID_FILE')
        ding=e.get('DINGTALK_ENABLED','false').strip().lower()
        if ding not in ('true','false'):raise ValueError('DINGTALK_ENABLED must be true or false')
        webhook=secret(e,'DINGTALK_WEBHOOK');signing=secret(e,'DINGTALK_SECRET')
        if ding=='true':
            from dingtalk import valid_webhook
            if not valid_webhook(webhook):raise ValueError('Configure valid DINGTALK_WEBHOOK; value hidden')
        keyword=e.get('DINGTALK_KEYWORD','Alpha雷达').strip()
        if not 1<=len(keyword)<=40 or any(ord(c)<32 for c in keyword):raise ValueError('DINGTALK_KEYWORD must be 1..40 visible characters')
        chain=e.get('RADAR_CHAIN','56')
        if chain!='all' and not re.fullmatch(r'[A-Za-z0-9_]+',chain):raise ValueError('Invalid RADAR_CHAIN')
        return cls(Path(e.get('RADAR_DATA','/data')),token,chat,chain,
                   integer(e,'POLL_SECONDS',180,60,86400),integer(e,'ENRICH_COUNT',12,1,1000),
                   integer(e,'SCAN_TIMEOUT_SECONDS',1800,60,7200),integer(e,'SIGNAL_TTL_SECONDS',3600,60,86400),
                   integer(e,'RETENTION_DAYS',30,1,3650),e.get('NOTIFY_NEW_INTERSECTION','true').lower()=='true',enabled,ding=='true',webhook,signing,keyword,integer(e,'FAST_POLL_SECONDS',60,30,300),integer(e,'FAST_TIMEOUT_SECONDS',150,30,600))

class Store:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.path=self.folder/'radar.sqlite'
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS scans(as_of TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,as_of TEXT,asset TEXT,event TEXT,payload TEXT);
                CREATE TABLE IF NOT EXISTS outbox(
                  id INTEGER PRIMARY KEY,key TEXT UNIQUE NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL,
                  state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
                  next_try REAL NOT NULL DEFAULT 0,sent REAL,error TEXT);
                CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS telegram_event AFTER INSERT ON events BEGIN
                  INSERT OR IGNORE INTO outbox(key,created,payload)
                    VALUES('market:'||NEW.asset||':'||NEW.event||':'||
                      CASE WHEN NEW.event='首次进入观察阈值' THEN COALESCE(CAST(json_extract(NEW.payload,'$.history_candle_end') AS TEXT),substr(NEW.as_of,1,10))
                      ELSE substr(NEW.as_of,1,10) END,
                      CAST(strftime('%s','now') AS REAL),
                      json_object('kind','market','event',NEW.event,'candidate',json(NEW.payload)));
                END;
            ''')
    @contextlib.contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def enqueue(self,key,payload,now=None):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO outbox(key,created,payload) VALUES(?,?,?)',(key,time.time() if now is None else now,json.dumps(payload,ensure_ascii=False)))
    def state(self,key,default=None):
        with self.db() as db:row=db.execute('SELECT value FROM runtime WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def put(self,key,value):
        self.write('INSERT OR REPLACE INTO runtime VALUES(?,?)',(key,json.dumps(value)))
    def write(self,sql,params=()):
        # A scan may hold SQLite's single writer slot while a delivery needs an ACK.
        for attempt in range(4):
            try:
                with self.db() as db:db.execute(sql,params)
                return
            except sqlite3.OperationalError as exc:
                if 'database is locked' not in str(exc).lower() or attempt==3:raise
                time.sleep(1)
    def scan_status(self,status,now=None,namespace='scan'):
        now=time.time() if now is None else now
        old=self.state(namespace,{'failures':0,'incident':None,'last_good':None})
        # Incident state and corresponding messages commit atomically.
        with self.db() as db:
            if status=='ok':
                if old.get('incident'):
                    db.execute('INSERT OR IGNORE INTO outbox(key,created,payload) VALUES(?,?,?)',('recovery:'+old['incident'],now,json.dumps({'kind':'ops','text':'✅ Alpha雷达 '+namespace+' 已恢复完整扫描。'})))
                old.update(failures=0,incident=None,last_good=now)
            else:
                old['failures']+=1
                if old['failures']>=3 and not old.get('incident'):
                    old['incident']=uuid.uuid4().hex
                    db.execute('INSERT OR IGNORE INTO outbox(key,created,payload) VALUES(?,?,?)',('failure:'+old['incident'],now,json.dumps({'kind':'ops','text':'⚠️ Alpha雷达 '+namespace+' 连续3轮扫描失败或数据不完整，请检查云端日志；旧快照不能代表最新行情。'})))
            old.update(status=status,checked=now)
            db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',(namespace,json.dumps(old)))
    def prune(self,days,now=None):
        cutoff=(time.time() if now is None else now)-days*86400
        iso=datetime.fromtimestamp(cutoff,timezone.utc).isoformat(timespec='seconds')
        with self.db() as db:
            db.execute('DELETE FROM scans WHERE as_of<? AND as_of<>(SELECT MAX(as_of) FROM scans)',(iso,))
            # Signals are long-term research evidence; only raw scans roll off.
            db.execute("DELETE FROM outbox WHERE created<? AND state<>'pending'",(cutoff,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='dingtalk_outbox'").fetchone():db.execute("DELETE FROM dingtalk_outbox WHERE created<? AND state<>'pending'",(cutoff,))
        # Only rolling live raw files, never the packaged historical research.
        raw=self.folder/'raw'
        if raw.exists() and not raw.is_symlink():
            for f in raw.glob('*.json'):
                if not f.is_symlink() and f.is_file() and f.stat().st_mtime<cutoff:f.unlink()

def number(value):
    return '未知' if value is None else f'{value:,.4g}'

def message(row):
    p=json.loads(row['payload'])
    if p['kind'] in ('ops','ranking'):text=p['text']
    else:
        v=p['candidate'];fx=v.get('history_features') or {}
        text=(f"🔎 Alpha雷达 · {p['event']}\n{v.get('symbol')} | 链 {v.get('chain_id')} | {v.get('futures_symbol')}\n"
              f"观察分：{v.get('screen_score')}（非收益概率）\n价格：{number(v.get('price'))} USD\n"
              f"流通市值：{number(v.get('market_cap'))} USD\n流动性：{number(v.get('liquidity'))} USD\n"
              f"100倍隐含市值：{number(v.get('implied_100x_market_cap'))} USD\n"
              f"日线量比：{number(fx.get('volume_ratio'))}\n本期资金费率（小数）：{number(v.get('funding_rate'))}\n"
              f"UTC：{v.get('as_of')}\n合约地址：{v.get('address')}\n"
              f"风险/阻断：{'；'.join(v.get('blocks',[])+v.get('risk_notes',[])) or '链上集中度/解锁/安全仍未核实'}\n"
              "研究提醒，不是买入指令；不保证100倍收益。")
    # Plain text: do not interpret token names as HTML/Markdown. Avoid Telegram's 4096 UTF-16 limit.
    text=text.encode('utf-16-le')[:6800].decode('utf-16-le',errors='ignore')
    return text+f"\n消息编号：{row['id']}"

class SendError(Exception):
    def __init__(self,code,retry=60):
        super().__init__(code)
        try:self.retry=max(1,min(int(retry),3600))
        except (ValueError,TypeError):self.retry=60

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def telegram_send(config,text):
    body=json.dumps({'chat_id':config.chat,'text':text,'link_preview_options':{'is_disabled':True}}).encode()
    request=urllib.request.Request('https://api.telegram.org/bot'+config.token+'/sendMessage',data=body,headers={'Content-Type':'application/json'})
    # Errors contain only controlled codes; never log a request URL, token, or exception repr.
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
            data=json.loads(response.read(65536))
    except urllib.error.HTTPError as e:
        retry=60
        try:retry=json.loads(e.read(65536)).get('parameters',{}).get('retry_after',60)
        except Exception:pass
        raise SendError('telegram_http_'+str(e.code),retry) from None
    except Exception:
        raise SendError('telegram_network_or_response_error') from None
    if not isinstance(data,dict) or not data.get('ok'):
        raise SendError('telegram_rejected',data.get('parameters',{}).get('retry_after',60) if isinstance(data,dict) else 60)


def deliver_one(store,config,send=telegram_send,now=None):
    now=time.time() if now is None else now
    with store.db() as db:
        db.execute("UPDATE outbox SET state='expired',error='signal_expired' WHERE state='pending' AND created<?",(now-config.ttl,))
        row=db.execute("SELECT * FROM outbox WHERE state='pending' AND next_try<=? ORDER BY id LIMIT 1",(now,)).fetchone()
    if not config.enabled or not row:return False
    payload=json.loads(row['payload'])
    if not config.notify_new and payload.get('event')=='新发现交集（不等于刚上市）':
        store.write("UPDATE outbox SET state='skipped' WHERE id=?",(row['id'],))
        return True
    try:send(config,message(row))
    except SendError as e:
        delay=max(e.retry,min(3600,30*2**min(row['attempts'],7)))
        store.write('UPDATE outbox SET attempts=attempts+1,next_try=?,error=? WHERE id=?',(now+delay,str(e),row['id']))
        store.put('telegram',{'checked':now,'status':'retry','error':str(e)})
    else:
        store.write("UPDATE outbox SET state='sent',sent=?,attempts=attempts+1,error=NULL WHERE id=?",(now,row['id']))
        store.put('telegram',{'checked':now,'status':'ok'})
    return True

class ScanProcess:
    def __init__(self,config,fast=False):self.config=config;self.process=None;self.started=None;self.log=None;self.fast=fast
    def start(self):
        c=self.config;self.started=time.time()
        self.log=open(c.data/('fast.log' if self.fast else 'scan.log'),'w',encoding='utf-8') # bounded to latest scan, no credential output
        command=[sys.executable,str(ROOT/'fast_lane.py'),'--data',str(c.data)] if self.fast else [sys.executable,str(ROOT/'cycle.py'),'scan','--chain',c.chain,'--data',str(c.data),'--enrich',str(c.enrich)]
        self.process=subprocess.Popen(command,stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True)
    def stop(self):
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid,signal.SIGTERM)
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(self.process.pid,signal.SIGKILL);self.process.wait()
        if self.log:self.log.close()
    def poll(self):
        if time.time()-self.started>(self.config.fast_timeout if self.fast else self.config.timeout) and self.process.poll() is None:self.stop();return 'timeout'
        code=self.process.poll()
        if code is None:return None
        self.log.close()
        if code:return 'failed'
        try:
            p=self.config.data/('fast-health.json' if self.fast else 'health.json')
            if p.stat().st_mtime<self.started:return 'stale_health'
            return json.loads(p.read_text())['status']
        except (OSError,ValueError,KeyError):return 'invalid_health'

def atomic(path,payload):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(payload),encoding='utf-8');tmp.replace(path)

def run(config):
    store=Store(config.data)
    import dingtalk
    from positions import initialize
    from alerts import reminders
    with store.db() as db:initialize(db)
    dingtalk.initialize(store)
    store.put('dingtalk',{'checked':time.time(),'status':'ready_waiting_for_signal' if config.ding_enabled else 'disabled_waiting_for_credentials'})
    if not config.enabled:store.put('telegram',{'checked':time.time(),'status':'disabled_waiting_for_credentials'})
    elif store.state('telegram',{}).get('status') in (None,'disabled_waiting_for_credentials'):
        store.put('telegram',{'checked':time.time(),'status':'ready_waiting_for_signal'})
    stop=threading.Event();beat=[time.monotonic()];notification_beat=[time.monotonic()]
    with open(config.data/'cloud.lock','w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another cloud monitor uses this data directory') from None
        def worker():
            from telegram_commands import poll
            next_poll=0;next_reminder=0;last_busy_log=0
            while not stop.is_set():
                notification_beat[0]=time.monotonic()
                try:
                    if time.monotonic()>=next_reminder:
                        reminders(store);next_reminder=time.monotonic()+15
                    deliver_one(store,config)
                    dingtalk.deliver_one(store,config)
                    if time.monotonic()>=next_poll:
                        poll(store,config)
                        next_poll=time.monotonic()+10
                except sqlite3.OperationalError as exc:
                    if 'database is locked' not in str(exc).lower():raise
                    if time.monotonic()-last_busy_log>=60:
                        print('notification_db_busy: retrying after scan write',flush=True)
                        last_busy_log=time.monotonic()
                notification_beat[0]=time.monotonic()
                stop.wait(1)
        def watchdog():
            while not stop.wait(10):
                if max(time.monotonic()-beat[0],time.monotonic()-notification_beat[0])>90:os._exit(2) # supervisor hung -> container restart
        notifier=threading.Thread(target=worker,daemon=True);notifier.start()
        threading.Thread(target=watchdog,daemon=True).start()
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
        scan=ScanProcess(config);fast_scan=ScanProcess(config,fast=True)
        due=0;fast_due=0;maintenance=0;running=False;fast_running=False
        try:
            while not stop.is_set():
                now=time.time();beat[0]=time.monotonic()
                if not notifier.is_alive():raise RuntimeError('Notification worker stopped')
                if not running and not fast_running and now>=fast_due:fast_scan.start();fast_running=True
                if fast_running:
                    fast_status=fast_scan.poll()
                    if fast_status is not None:
                        store.scan_status(fast_status,namespace='fast');fast_running=False;fast_due=max(now+5,fast_scan.started+config.fast_interval)
                if not running and not fast_running and now>=due:scan.start();running=True
                if running:
                    status=scan.poll()
                    if status is not None:
                        store.scan_status(status);running=False;due=now+config.interval
                        print(json.dumps({'scan':status,'at':now}),flush=True)
                if now-maintenance>86400 and not running:store.prune(config.retention);maintenance=now
                atomic(config.data/'cloud-health.json',{'heartbeat':now,'scanner_running':running,'scan_started':scan.started,
                       'scan':store.state('scan',{}),'fast':store.state('fast',{}),'fast_running':fast_running,'telegram':store.state('telegram',{}),'commands':store.state('commands',{}),'dingtalk':store.state('dingtalk',{})})
                stop.wait(2)
        finally:
            stop.set();scan.stop();fast_scan.stop();notifier.join(timeout=25)

def discover_chat_ids(token):
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token):raise ValueError('Configure TELEGRAM_BOT_TOKEN first')
    try:
        req=urllib.request.Request('https://api.telegram.org/bot'+token+'/getUpdates',data=b'{"timeout":0}',headers={'Content-Type':'application/json'})
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as response:data=json.loads(response.read(1000000))
        if not data.get('ok'):raise ValueError('Rejected')
        ids={}
        for update in data.get('result',[]):
            for field in ('message','channel_post','my_chat_member'):
                chat=update.get(field,{}).get('chat',{})
                if 'id' in chat:ids[str(chat['id'])]=chat.get('type','unknown')
        print(json.dumps(ids,ensure_ascii=False) if ids else '未找到会话。请先给机器人发送 /start 后重试；若已配置 webhook，需要用该系统提供的 chat_id。')
    except Exception:raise RuntimeError('Telegram chat-id lookup failed; check token, network or existing webhook') from None

def main():
    p=argparse.ArgumentParser();p.add_argument('--chat-ids',action='store_true');p.add_argument('--check-config',action='store_true');p.add_argument('--test-telegram',action='store_true');p.add_argument('--test-dingtalk',action='store_true');args=p.parse_args()
    try:
        if args.chat_ids:discover_chat_ids(secret(os.environ,'TELEGRAM_BOT_TOKEN'));return
        c=Config.load()
        if args.check_config:print('Configuration valid (credentials not displayed)');return
        if args.test_dingtalk:
            if not c.ding_enabled:raise ValueError('Set DINGTALK_ENABLED=true before testing')
            from dingtalk import send
            send(c,'✅ 云服务器钉钉连接测试，不是交易信号。');print('DingTalk accepted test message');return
        if args.test_telegram and not c.enabled:raise ValueError('Set TELEGRAM_ENABLED=true before testing')
        if args.test_telegram:telegram_send(c,'✅ Alpha雷达 Telegram 连接测试。此消息不是交易信号。');print('Telegram accepted test message');return
        run(c)
    except Exception as e:
        # Config exceptions might include secret-file paths, but never credential contents.
        print('cloud_error:'+type(e).__name__+((': '+str(e)) if isinstance(e,(ValueError,RuntimeError,SendError)) else ''),file=sys.stderr)
        raise SystemExit(1)

if __name__=='__main__':main()
