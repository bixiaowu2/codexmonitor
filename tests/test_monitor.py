import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import x_monitor as m


def tweet(account, number):
    return m.Tweet(account, str(number), f'https://x.com/{account}/status/{number}', '测试', '2026-09-25T08:00:00Z')


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.state = m.load_state(self.path)
        self.a = m.WebhookTarget('wecom', 'http://127.0.0.1/w')
        self.b = m.WebhookTarget('dingtalk', 'http://127.0.0.1/d')
        self.routes = {'*': [self.a, self.b]}

    def test_independent_baselines_new_accounts_and_old_pinned_posts(self):
        m.ingest(self.state, 'a', [tweet('a', 10)], self.routes, self.path)
        # b fails repeatedly; a still generates notifications.
        m.ingest(self.state, 'a', [tweet('a', 11)], self.routes, self.path)
        self.assertIn('a:11', self.state['pending'])
        # b's first successful scrape is a baseline, without a historical flood.
        m.ingest(self.state, 'b', [tweet('b', 20)], self.routes, self.path)
        self.assertNotIn('b:20', self.state['pending'])
        # Newly appearing old pinned card is not a new post.
        m.ingest(self.state, 'a', [tweet('a', 9), tweet('a', 11)], self.routes, self.path)
        self.assertNotIn('a:9', self.state['pending'])

    def test_partial_failure_survives_restart_and_card_disappearance(self):
        m.ingest(self.state, 'a', [tweet('a', 10)], self.routes, self.path)
        m.ingest(self.state, 'a', [tweet('a', 11)], self.routes, self.path)
        calls = []
        def partial(t, target):
            calls.append(target.kind)
            if target == self.b:
                raise RuntimeError('reject')
        with patch.object(m, 'send_target', side_effect=partial):
            self.assertFalse(m.deliver_pending(self.state, self.routes, self.path))
        self.assertEqual(calls, ['wecom', 'dingtalk'])
        state = m.load_state(self.path)
        self.assertEqual(state['pending']['a:11']['remaining'], [m.target_key(self.b)])
        state['pending']['a:11']['next_attempt'] = 0
        with patch.object(m, 'send_target') as send:
            self.assertTrue(m.deliver_pending(state, self.routes, self.path))
            send.assert_called_once_with(tweet('a', 11), self.b)
        self.assertFalse(m.load_state(self.path)['pending'])

    def test_empty_and_corrupt_state_do_not_reset_baselines(self):
        with self.assertRaises(RuntimeError):
            m.ingest(self.state, 'a', [], self.routes, self.path)
        self.assertFalse(self.state['initialized_accounts'])
        self.path.write_text('{broken')
        with self.assertRaises(RuntimeError):
            m.load_state(self.path)

    def test_no_target_is_not_delivery_success(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                m.notify(tweet('a', 1), {})

    def test_old_state_migration(self):
        self.path.write_text(json.dumps({'seen': {'a': {'10': 't'}, 'b': {}}, 'initialized': True}))
        state = m.load_state(self.path)
        self.assertEqual(state['initialized_accounts'], ['a'])
        self.assertEqual(state['watermarks']['a'], 10)

    def test_webhook_test_covers_default_and_account_routes(self):
        self.path.write_text(json.dumps({'default':[{'type':'wecom','url':'http://127.0.0.1/w'}],
            'accounts':{'a':[{'type':'dingtalk','url':'http://127.0.0.1/d'}]}}))
        with patch.dict(os.environ, {'ROUTES_FILE':str(self.path)}, clear=True), patch('sys.argv', ['x_monitor.py','--test-webhooks']), patch.object(m, 'send_target') as send:
            self.assertEqual(m.run(), 0)
            self.assertEqual(send.call_count, 2)


class HTTPTests(unittest.TestCase):
    def test_real_http_acknowledgment_validation(self):
        replies = [b'{"errcode":0}', b'{"errcode":40001}', b'<html>login</html>', b'{}']
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(replies.pop(0))
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/'
            m.post_json(url, {})
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    m.post_json(url, {})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()



class BrowserTests(unittest.TestCase):
    def test_quote_card_uses_timestamp_link_and_primary_text(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content('''<article data-testid="tweet">
                  <a href="/other/status/90">earlier reference</a>
                  <a href="/demo/status/100"><time datetime="2026-09-25T10:00:00Z">now</time></a>
                  <div data-testid="tweetText">主帖中文</div>
                  <div data-testid="tweetText">引用内容</div></article>''')
                result = m.extract_tweet('demo', page.locator('article'))
                self.assertIsNotNone(result)
                self.assertEqual(result.tweet_id, '100')
                self.assertEqual(result.text, '主帖中文')
            finally:
                browser.close()

    def test_new_x_timeline_layout_uses_outer_post_not_quoted_post(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content('''<div data-timeline-entry data-href="/demo/status/2103507442395209822">
                  <article><div dir="auto" class="whitespace-pre-wrap">实际主帖</div>
                  <div role="link" data-timeline-entry data-href="/quoted/status/1900000000000000000">
                    <a href="/quoted/status/1900000000000000000"><time datetime="2024-01-01T00:00:00Z">old</time></a>
                    <div dir="auto" class="whitespace-pre-wrap">引用旧帖</div>
                  </div></article></div>''')
                card = page.locator(m.CARD_SELECTOR)
                self.assertEqual(card.count(), 1)
                result = m.extract_tweet('demo', card)
                self.assertEqual(result.tweet_id, '2103507442395209822')
                self.assertEqual(result.url, 'https://x.com/demo/status/2103507442395209822')
                self.assertEqual(result.text, '实际主帖')
                self.assertTrue(result.published_at.startswith('2026-09-25'))
            finally:
                browser.close()

class TelegramTests(unittest.TestCase):
    def test_telegram_http_response_and_plain_text(self):
        received = []
        responses = [b'{"ok":true,"result":{"message_id":1}}', b'{"ok":false,"error_code":429}', b'{}']
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(responses.pop(0))
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            target = m.WebhookTarget('telegram', f'http://127.0.0.1:{server.server_port}/sendMessage', chat_id='-100123')
            m.send_target(tweet('a', 1), target)
            self.assertEqual(received[0]['chat_id'], '-100123')
            self.assertIn('https://x.com/a/status/1', received[0]['text'])
            self.assertNotIn('parse_mode', received[0])
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    m.send_target(tweet('a', 1), target)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_distinct_chats_and_telegram_failure_do_not_block_dingtalk(self):
        t1 = m.WebhookTarget('telegram', 'https://api.telegram.org/bot123:test/sendMessage', chat_id='-1001')
        t2 = m.WebhookTarget('telegram', t1.url, chat_id='-1002')
        self.assertNotEqual(m.target_key(t1), m.target_key(t2))
        ding = m.WebhookTarget('dingtalk', 'http://127.0.0.1/d')
        calls = []
        def fail_telegram(t, target):
            calls.append(target.kind)
            if target.kind == 'telegram':
                raise RuntimeError('temporary outage')
        with patch.object(m, 'send_target', side_effect=fail_telegram):
            with self.assertRaises(RuntimeError):
                m.notify(tweet('a', 1), {'*':[t1, ding]})
        self.assertEqual(calls, ['telegram', 'dingtalk'])

    def test_bot_config_requires_destination(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'123:abc'}, clear=True):
            with self.assertRaises(RuntimeError):
                m.global_targets()
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'123:abc','TELEGRAM_CHAT_ID':'-1001'}, clear=True):
            targets = m.global_targets()
            self.assertEqual(targets[0].chat_id, '-1001')

    def test_health_alert_cooldown_recovery_and_stale_check(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'notify') as notify:
            path = Path(d)/'state.json'
            state = m.load_state(path)
            self.assertEqual(m.health_check(path, ['a']), 1)
            m.record_scrape_health(state, 'a', True, {}, path)
            self.assertEqual(m.health_check(path, ['a']), 0)
            for _ in range(3):
                m.record_scrape_health(state, 'a', False, {}, path)
            self.assertEqual(notify.call_count, 1)
            self.assertEqual(notify.call_args.args[0].kind, '监控状态')
            m.record_scrape_health(state, 'a', False, {}, path)
            self.assertEqual(notify.call_count, 1)
            m.record_scrape_health(state, 'a', True, {}, path)
            self.assertEqual(notify.call_count, 2)
            state['health']['accounts']['a']['last_success'] = 0
            m.save_state(path, state)
            self.assertEqual(m.health_check(path, ['a']), 1)


class DingTalkKeywordTests(unittest.TestCase):
    def test_keyword_in_title_and_body_for_posts_and_alerts(self):
        target = m.WebhookTarget('dingtalk', 'http://127.0.0.1/d', keyword='DT')
        with patch.object(m, 'post_json') as post:
            for item in (tweet('a', 1), m.Tweet('a','health','https://x.com/a','抓取异常','','监控状态')):
                m.send_target(item, target)
                payload = post.call_args.args[1]['markdown']
                self.assertTrue(payload['title'].startswith('DT | '))
                self.assertTrue(payload['text'].startswith('DT\n'))


if __name__ == '__main__':
    unittest.main()
