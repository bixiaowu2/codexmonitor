import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import public_feed as f

class Tweet:
    def __init__(self, account, tweet_id, url, text='text', published_at=None):
        self.account=account; self.tweet_id=str(tweet_id); self.url=url; self.text=text
        self.published_at=published_at or '2026-09-26T00:00:00+00:00'

class PublicFeedTests(unittest.TestCase):
    def test_registry_loads_and_rejects_bad_chain(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'accounts.json'
            path.write_text(json.dumps({'accounts':[{'handle':'lookonchain','chains':['bsc'],'category':'research','evidence':['https://www.lookonchain.com'],'interval_seconds':600}]}))
            self.assertEqual(f.load_accounts(path)['lookonchain']['chains'],['bsc'])
            path.write_text(json.dumps({'accounts':[{'handle':'bad','chains':['unknown'],'category':'research','evidence':['https://x.com/a'],'interval_seconds':600}]}))
            with self.assertRaises(ValueError): f.load_accounts(path)
    def test_public_posts_are_stored_and_duplicates_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'feed.sqlite'; feed=f.PublicFeed(db); now=time.time()
            tweet=Tweet('lookonchain','123','https://x.com/lookonchain/status/123',published_at='2026-09-26T00:00:00+00:00')
            # Use a current timestamp so the feed's 24h freshness gate accepts it.
            tweet.published_at=__import__('datetime').datetime.fromtimestamp(now-30,__import__('datetime').timezone.utc).isoformat()
            self.assertEqual(feed.success('lookonchain',[tweet],now=now),1)
            self.assertEqual(feed.success('lookonchain',[tweet],now=now),0)
            with feed.db() as conn: self.assertEqual(conn.execute('select count(*) from posts').fetchone()[0],1)
    def test_wrong_author_url_is_not_exported(self):
        with tempfile.TemporaryDirectory() as d:
            feed=f.PublicFeed(Path(d)/'feed.sqlite'); now=time.time()
            tweet=Tweet('lookonchain','123','https://x.com/other/status/123',published_at=__import__('datetime').datetime.fromtimestamp(now-30,__import__('datetime').timezone.utc).isoformat())
            self.assertEqual(feed.success('lookonchain',[tweet],now=now),0)
    def test_failures_backoff_and_due_fairness(self):
        with tempfile.TemporaryDirectory() as d:
            feed=f.PublicFeed(Path(d)/'feed.sqlite'); now=time.time()
            registry={x:{'priority':10} for x in ('a','b')}
            feed.failure('a','timeout',interval=600,now=now)
            self.assertNotIn('a',feed.due(registry,now=now))
            self.assertIn('b',feed.due(registry,now=now))

if __name__=='__main__': unittest.main()

class ExtraAccountDeliveryTests(unittest.TestCase):
    def test_new_account_baseline_then_both_original_targets(self):
        import x_monitor as m
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.json'; state=m.load_state(path)
            targets=[m.WebhookTarget('telegram','https://example.invalid/t',chat_id='1'),m.WebhookTarget('dingtalk','https://example.invalid/d')]
            routes={'*':targets}; feed=MagicMock()
            a=m.Tweet('lookonchain','100','https://x.com/lookonchain/status/100','baseline','2026-09-26T00:00:00Z')
            b=m.Tweet('lookonchain','101','https://x.com/lookonchain/status/101','new','2026-09-26T00:00:01Z')
            with patch.object(m,'scrape_account',side_effect=[[a],[a,b]]),patch.object(m,'send_target') as send:
                m.collect_extra_account(None,'lookonchain',{'interval_seconds':120},state,routes,path,feed)
                self.assertEqual(send.call_count,0)
                m.collect_extra_account(None,'lookonchain',{'interval_seconds':120},state,routes,path,feed)
                self.assertEqual([c.args[1].kind for c in send.call_args_list],['telegram','dingtalk'])
                self.assertEqual({c.args[0].tweet_id for c in send.call_args_list},{'101'})
            self.assertFalse(state['pending'])
    def test_export_failure_does_not_suppress_notification(self):
        import x_monitor as m
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.json'; state=m.load_state(path)
            routes={'*':[m.WebhookTarget('telegram','https://example.invalid/t',chat_id='1')]}
            a=m.Tweet('lookonchain','100','https://x.com/lookonchain/status/100','a','2026-09-26T00:00:00Z')
            b=m.Tweet('lookonchain','101','https://x.com/lookonchain/status/101','b','2026-09-26T00:00:01Z')
            m.ingest(state,'lookonchain',[a],routes,path)
            feed=MagicMock();feed.success.side_effect=OSError('storage unavailable')
            with patch.object(m,'scrape_account',return_value=[b]),patch.object(m,'send_target') as send:
                m.collect_extra_account(None,'lookonchain',{'interval_seconds':120},state,routes,path,feed)
                send.assert_called_once()
