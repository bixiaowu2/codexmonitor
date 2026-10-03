import json
import tempfile
import time
import unittest
from unittest.mock import patch

from gmgn import Client, gmgn_chain
from net import FetchError


class TestGmgn(unittest.TestCase):
    def test_chain_aliases(self):
        self.assertEqual(gmgn_chain('solana'), 'sol')
        self.assertEqual(gmgn_chain('xlayer'), 'hyperevm')
        self.assertEqual(gmgn_chain('bsc'), 'bsc')

    def test_disabled_client_does_not_request(self):
        client = Client('')
        self.assertFalse(client.enabled)
        self.assertEqual(client.enrich([{'chain': 'bsc', 'address': '0x' + 'a' * 40}])['status'], 'disabled')

    def test_query_request_uses_api_key_and_ephemeral_query_auth(self):
        client = Client('secret-api-key', base_url='https://gmgn.invalid')
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, _): return json.dumps({'code': 0, 'data': {'price': '1.25'}}).encode()

        def fake_open(request, timeout):
            captured['url'] = request.full_url
            captured['headers'] = dict(request.headers)
            captured['timeout'] = timeout
            return Response()

        with patch('gmgn.urllib.request.urlopen', fake_open):
            self.assertEqual(client.token_info('bsc', '0x' + 'a' * 40)['price'], '1.25')
        self.assertIn('X-apikey', captured['headers'])
        self.assertEqual(captured['headers']['X-apikey'], 'secret-api-key')
        self.assertIn('timestamp=', captured['url'])
        self.assertIn('client_id=', captured['url'])
        self.assertNotIn('secret-api-key', captured['url'])

    def test_enrich_keeps_gmgn_source_separate(self):
        client = Client('key', max_requests=4)
        pair = {'chain': 'bsc', 'address': '0x' + 'a' * 40, 'liquidity_usd': 1000,
                'volume_1h': 2000, 'price_usd': None}
        with patch.object(client, 'token_info', return_value={'price': '2.5', 'market_cap': '100000'}), \
             patch.object(client, 'token_security', return_value={'is_honeypot': '0'}):
            result = client.enrich([pair], 1)
        self.assertEqual(result['enriched'], 1)
        self.assertEqual(pair['price_usd'], 2.5)
        self.assertEqual(pair['gmgn']['gmgn_market_cap'], 100000)
        self.assertIn('security', pair['gmgn'])

    def test_nested_price_and_exact_address(self):
        client = Client('key', max_requests=4)
        address = '0x' + 'a' * 40
        pair = {'chain': 'bsc', 'address': address, 'liquidity_usd': 1000,
                'volume_1h': 2000, 'price_usd': None}
        with patch.object(client, 'token_info', return_value={
                'address': address.upper(), 'price': {'price': '0.00025'}}), \
             patch.object(client, 'token_security', return_value={}):
            self.assertEqual(client.enrich([pair], 1)['enriched'], 1)
        self.assertEqual(pair['price_usd'], 0.00025)

    def test_mismatched_address_never_supplies_price(self):
        client = Client('key', max_requests=4)
        pair = {'chain': 'bsc', 'address': '0x' + 'a' * 40,
                'liquidity_usd': 1000, 'volume_1h': 2000, 'price_usd': None}
        with patch.object(client, 'token_info', return_value={
                'address': '0x' + 'b' * 40, 'price': {'price': '100'}}), \
             patch.object(client, 'token_security') as security:
            self.assertEqual(client.enrich([pair], 1)['enriched'], 0)
        security.assert_not_called()
        self.assertIsNone(pair['price_usd'])
        self.assertEqual(pair['gmgn']['error'], 'gmgn_address_mismatch')

    def test_rate_limit_enters_backoff_without_secret_in_error(self):
        client = Client('secret-api-key')
        with patch('gmgn.urllib.request.urlopen', side_effect=type('E', (Exception,), {'code': 429})()):
            with self.assertRaises(FetchError) as ctx:
                client.token_info('bsc', '0x' + 'a' * 40)
        self.assertEqual(ctx.exception.code, 'gmgn_http_429')
        self.assertGreater(client.backoff_until, time.time())
        self.assertNotIn('secret-api-key', client.last_error)


if __name__ == '__main__':
    unittest.main()
