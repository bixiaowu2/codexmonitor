import argparse,json,sqlite3,tempfile,time,unittest,urllib.error,io
from pathlib import Path
from unittest.mock import patch
import cloud

ENV={'TELEGRAM_BOT_TOKEN':'123456:FAKE_TEST_TOKEN','TELEGRAM_CHAT_ID':'1234','RADAR_DATA':'/tmp/not-used'}

def event(store,asset='56:0xabc',candle=100):
    v={'symbol':'TEST','chain_id':'56','address':'0xabc','history_candle_end':candle,'as_of':'2026-09-25T00:00:00+00:00','screen_score':100,'blocks':[],'risk_notes':[]}
    with store.db() as db:
        db.execute('INSERT INTO events(as_of,asset,event,payload) VALUES(?,?,?,?)',(v['as_of'],asset,'首次进入观察阈值',json.dumps(v)))

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=cloud.Store(self.temp.name);self.config=cloud.Config.load(ENV)
    def tearDown(self):self.temp.cleanup()
    def rows(self):
        with self.store.db() as db:return list(db.execute('SELECT * FROM outbox ORDER BY id'))
    def test_trigger_durable_dedup_across_restart(self):
        event(self.store);event(self.store)
        restarted=cloud.Store(self.temp.name);event(restarted)
        self.assertEqual(len(self.rows()),1)
        event(restarted,candle=101);self.assertEqual(len(self.rows()),2)
    def test_event_and_outbox_rollback_together(self):
        try:
            with self.store.db() as db:
                db.execute("INSERT INTO events(as_of,asset,event,payload) VALUES('2026-01-01','x','y','{}')")
                raise RuntimeError()
        except RuntimeError:pass
        self.assertEqual(self.rows(),[])
    def test_install_does_not_backfill_old_events(self):
        other=Path(self.temp.name)/'old';other.mkdir()
        with sqlite3.connect(other/'radar.sqlite') as db:
            db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,as_of TEXT,asset TEXT,event TEXT,payload TEXT)')
            db.execute("INSERT INTO events VALUES(1,'old','a','b','{}')")
        migrated=cloud.Store(other)
        with migrated.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
    def test_retry_then_send_preserves_message(self):
        event(self.store);now=time.time()
        def fail(*_):raise cloud.SendError('telegram_http_429',120)
        cloud.deliver_one(self.store,self.config,send=fail,now=now)
        row=self.rows()[0];self.assertEqual(row['state'],'pending');self.assertGreaterEqual(row['next_try'],now+120)
        calls=[];cloud.deliver_one(self.store,self.config,send=lambda c,t:calls.append(t),now=now+121)
        self.assertEqual(len(calls),1);self.assertEqual(self.rows()[0]['state'],'sent')
        cloud.deliver_one(self.store,self.config,send=lambda c,t:calls.append(t),now=now+122)
        self.assertEqual(len(calls),1)
    def test_runtime_write_retries_transient_sqlite_lock(self):
        original=self.store.db;calls=[]
        def db():
            calls.append(1)
            if len(calls)==1:raise sqlite3.OperationalError('database is locked')
            return original()
        with patch.object(self.store,'db',side_effect=db),patch('cloud.time.sleep'):
            self.store.put('lock-retry',{'status':'ok'})
        self.assertEqual(len(calls),2)
        self.assertEqual(self.store.state('lock-retry'),{'status':'ok'})
    def test_expired_signal_never_sent(self):
        self.store.enqueue('old',{'kind':'ops','text':'old'},now=0)
        cloud.deliver_one(self.store,self.config,send=lambda *_:self.fail('expired sent'),now=self.config.ttl+1)
        self.assertEqual(self.rows()[0]['state'],'expired')
    def test_fault_once_and_recovery_once(self):
        for _ in range(6):self.store.scan_status('failed')
        self.assertEqual(len(self.rows()),1)
        restarted=cloud.Store(self.temp.name);restarted.scan_status('ok');restarted.scan_status('ok')
        self.assertEqual(len(self.rows()),2)
        self.assertEqual(restarted.state('scan')['failures'],0)
    def test_retention_preserves_latest_and_pending(self):
        with self.store.db() as db:
            db.execute("INSERT INTO scans VALUES('2000-01-01','{}')")
            db.execute("INSERT INTO scans VALUES('2000-01-02','{}')")
        self.store.enqueue('pending',{'kind':'ops','text':'pending'},now=0)
        self.store.prune(30)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM scans').fetchone()[0],1)
        self.assertEqual(self.rows()[0]['state'],'pending')
    def test_plaintext_size_unicode_safe(self):
        self.store.enqueue('long',{'kind':'ops','text':'😀'*5000})
        msg=cloud.message(self.rows()[0]);self.assertLess(len(msg.encode('utf-16-le'))//2,4096)
    def test_http_errors_do_not_leak_token(self):
        error=urllib.error.HTTPError('https://api.telegram.org/bot'+self.config.token+'/sendMessage',401,'unauthorized',{},io.BytesIO(b'{}'))
        with patch('cloud.urllib.request.OpenerDirector.open',side_effect=error):
            with self.assertRaises(cloud.SendError) as caught:cloud.telegram_send(self.config,'test')
        self.assertNotIn(self.config.token,str(caught.exception));self.assertIn('401',str(caught.exception))
    def test_config_invalid_no_token_echo(self):
        with self.assertRaises(ValueError) as caught:cloud.Config.load(dict(ENV,TELEGRAM_BOT_TOKEN='SECRET_INVALID_TOKEN'))
        self.assertNotIn('SECRET_INVALID_TOKEN',str(caught.exception))
        with self.assertRaises(ValueError):cloud.Config.load(dict(ENV,POLL_SECONDS='0'))
    def test_scan_timeout_kills_child_group(self):
        c=self.config
        with patch('cloud.os.killpg') as kill,patch('cloud.subprocess.Popen') as process:
            proc=cloud.ScanProcess(c);proc.started=time.time()-c.timeout-1;proc.process=process.return_value
            proc.process.poll.return_value=None
            self.assertEqual(proc.poll(),'timeout');kill.assert_called_once()
    def test_partial_failure_is_degraded_not_recovered(self):
        for _ in range(3):self.store.scan_status('failed')
        self.store.scan_status('partial')
        self.assertIsNotNone(self.store.state('scan')['incident']);self.assertEqual(len(self.rows()),1)

if __name__=='__main__':unittest.main()
