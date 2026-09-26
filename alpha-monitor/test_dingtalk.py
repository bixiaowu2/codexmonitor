import json,tempfile,time,unittest,urllib.error,io
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
import cloud,dingtalk

class DingTalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=cloud.Store(self.tmp.name);dingtalk.initialize(self.store)
        self.config=cloud.Config.load({'TELEGRAM_ENABLED':'false','DINGTALK_ENABLED':'true','DINGTALK_WEBHOOK':'https://oapi.dingtalk.com/robot/send?access_token=FAKE_SECRET','DINGTALK_SECRET':'TEST_SIGNING'})
    def tearDown(self):self.tmp.cleanup()
    def test_fanout_does_not_copy_private_command_reply(self):
        self.store.enqueue('market:test',{'kind':'ops','text':'test'})
        self.store.enqueue('command:1:0',{'kind':'ops','text':'private'})
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM dingtalk_outbox').fetchone()[0],1)
    def test_other_channel_failure_does_not_resend_dingtalk(self):
        self.store.enqueue('market:test',{'kind':'ops','text':'test'});calls=[]
        dingtalk.deliver_one(self.store,self.config,send_fn=lambda c,t:calls.append(t))
        dingtalk.deliver_one(self.store,self.config,send_fn=lambda c,t:calls.append(t))
        self.assertEqual(len(calls),1)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM outbox').fetchone()[0],'pending')
            self.assertEqual(db.execute('SELECT state FROM dingtalk_outbox').fetchone()[0],'sent')
    def test_failure_retry_and_expiry(self):
        now=time.time();self.store.enqueue('test',{'kind':'ops','text':'test'},now=now)
        def fail(*_):raise dingtalk.DingError('dingtalk_rejected')
        dingtalk.deliver_one(self.store,self.config,send_fn=fail,now=now)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT attempts FROM dingtalk_outbox').fetchone()[0],1)
        dingtalk.deliver_one(self.store,self.config,send_fn=lambda *_:self.fail('expired message sent'),now=now+self.config.ttl+1)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT state FROM dingtalk_outbox').fetchone()[0],'expired')
    def test_signing_and_unsafe_urls(self):
        url=dingtalk.signed_url(self.config.ding_webhook,self.config.ding_secret,1000)
        q=parse_qs(urlsplit(url).query)
        self.assertEqual(q['timestamp'],['1000000']);self.assertEqual(q['access_token'],['FAKE_SECRET']);self.assertEqual(len(q['sign'][0]),44)
        for url in ['http://oapi.dingtalk.com/robot/send?access_token=x','https://evil.example/robot/send?access_token=x','https://u@oapi.dingtalk.com/robot/send?access_token=x']:
            self.assertFalse(dingtalk.valid_webhook(url))
    def test_errors_never_expose_webhook(self):
        exc=urllib.error.HTTPError(self.config.ding_webhook,403,'error',{},io.BytesIO(b''))
        with patch('dingtalk.urllib.request.OpenerDirector.open',side_effect=exc):
            with self.assertRaises(dingtalk.DingError) as err:dingtalk.send(self.config,'test')
        self.assertNotIn('FAKE_SECRET',str(err.exception))
    def test_install_does_not_backfill_prior_alerts(self):
        other=cloud.Store(self.tmp.name+'/other');other.enqueue('old',{'kind':'ops','text':'old'});dingtalk.initialize(other)
        with other.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM dingtalk_outbox').fetchone()[0],0)

    def test_existing_keyword_is_in_actual_payload(self):
        config=replace(self.config,ding_keyword='DT')
        with patch('dingtalk.urllib.request.OpenerDirector.open') as opened:
            opened.return_value.__enter__.return_value.read.return_value=b'{"errcode":0}'
            dingtalk.send(config,'test signal')
            payload=json.loads(opened.call_args.args[0].data)
            self.assertTrue(payload['text']['content'].startswith('DT'))
            self.assertFalse(payload['at']['isAtAll'])

if __name__=='__main__':unittest.main()
