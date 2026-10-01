import json
import tempfile
import time
import unittest
from unittest.mock import patch

from config import Config
from inputs import valid_event
from post_rules import evaluate
from radar import cycle, fmt
from sources import Collector, normalize_pair
from storage import Store
from wallet_rules import evidence


class PostRulesTest(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.token = '0x' + 'a' * 40
        self.pair = {'chain': 'bsc', 'address': self.token, 'created_at': self.now-600,
                     'market_cap': 40000, 'fdv': 45000, 'liquidity_usd': 30000,
                     'volume_6h': 50000, 'volume_1h': 40000, 'price_usd': 1,
                     'symbol': 'TEST', 'pair_address': '0x'+'b'*40, 'source': 'test',
                     'fetched_at': self.now, 'safety_eligible': True}

    def wallet(self, index=1, **overrides):
        tx = '0x' + str(index) * 64
        result = {'chain': 'bsc', 'token': self.token, 'address': '0x'+str(index)*40,
                  'observed_at': self.now, 'last_active_at': self.now-100,
                  'recent_transaction_times': [self.now-1200+i*60 for i in range(6)],
                  'source': 'https://bscscan.com/tx/'+tx, 'tx_hash': tx,
                  'action': 'buy', 'confirmed_swap': True, 'token_amount': 10,
                  'remaining_balance': 5, 'independence_cluster': str(index),
                  'independence_source': 'https://example.org/audited-evidence'}
        result.update(overrides)
        return result

    def test_three_independent_holdings_observe_without_score_bonus(self):
        result = evidence(self.pair, {str(i): self.wallet(i) for i in (1,2,3)}, self.now)
        self.assertTrue(result['observation_eligible'])
        self.assertEqual(result['score_bonus'], 0)
        self.assertIn('not_independently_fetched', result['verification'])

    def test_related_wallets_count_as_one(self):
        result = evidence(self.pair, {str(i): self.wallet(i, independence_cluster='same') for i in (1,2,3)}, self.now)
        self.assertEqual(result['independent_buy_clusters'], 1)
        self.assertFalse(result['observation_eligible'])

    def test_transfer_not_a_buy(self):
        self.assertEqual(evidence(self.pair, {'1':self.wallet(confirmed_swap=False)}, self.now)['status'], 'unknown')

    def test_seconds_bot_excluded(self):
        event = self.wallet(recent_transaction_times=[self.now-10+i for i in range(6)])
        self.assertEqual(evidence(self.pair, {'1':event}, self.now)['status'], 'unknown')

    def test_missing_history_is_not_human_wallet(self):
        self.assertEqual(evidence(self.pair, {'1':self.wallet(recent_transaction_times=[])}, self.now)['status'], 'unknown')

    def test_expired_future_and_wrong_chain_rejected(self):
        for kwargs in ({'observed_at': self.now-3601}, {'observed_at':self.now+1}, {'chain':'arc'}):
            self.assertEqual(evidence(self.pair, {'1':self.wallet(**kwargs)}, self.now)['status'], 'unknown')

    def test_fake_explorer_malformed_url_and_testnet_rejected(self):
        for source in ('https://bscscan.com.evil/tx/'+'0x'+'1'*64, 'https://[', 'https://testnet.arcscan.app/tx/'+'0x'+'1'*64):
            self.assertEqual(evidence(self.pair, {'1':self.wallet(source=source)}, self.now)['status'], 'unknown')

    def test_selling_or_missing_safety_excludes_observation(self):
        wallets={str(i):self.wallet(i) for i in (1,2,3)}
        wallets['4']=self.wallet(4, action='sell',remaining_balance=0)
        self.assertFalse(evidence(self.pair, wallets,self.now)['observation_eligible'])
        self.assertFalse(evidence(dict(self.pair,safety_eligible=False), {str(i):self.wallet(i) for i in (1,2,3)},self.now)['observation_eligible'])

    def test_early_filter_matches_without_score_bonus(self):
        result=evaluate(self.pair,self.now)
        self.assertTrue(result['matched']);self.assertTrue(result['observation_eligible'])
        self.assertEqual(result['score_bonus'],0)

    def test_fdv_does_not_replace_missing_cap(self):
        result=evaluate(dict(self.pair,market_cap=None),self.now)
        self.assertEqual(result['status'],'unknown')
        self.assertIn('market_cap',result['missing'])

    def test_missing_six_hour_volume_not_estimated(self):
        self.assertEqual(evaluate(dict(self.pair,volume_6h=None),self.now)['status'],'unknown')

    def test_numeric_corruption_and_filter_boundaries(self):
        for kwargs in ({'market_cap':float('nan')},{'market_cap':True},{'created_at':self.now+1}):
            self.assertEqual(evaluate(dict(self.pair,**kwargs),self.now)['status'],'unknown')
        for kwargs in ({'market_cap':50000},{'liquidity_usd':10000},{'volume_6h':10000},{'created_at':self.now-299},{'created_at':self.now-86401}):
            self.assertFalse(evaluate(dict(self.pair,**kwargs),self.now)['matched'])

    def test_milliseconds_and_safety_unknown(self):
        result=evaluate(dict(self.pair,created_at=(self.now-600)*1000,safety_eligible=False),self.now)
        self.assertTrue(result['matched']);self.assertFalse(result['observation_eligible'])

    def test_parser_keeps_six_hour_volume(self):
        self.assertEqual(normalize_pair({'volume':{'h6':'12000'}},'bsc','test')['volume_6h'],12000)

    def test_malformed_evidence_does_not_crash(self):
        self.assertFalse(valid_event(['not_a_dict'],self.now))
        self.assertEqual(evidence(self.pair,{'1':None},self.now)['status'],'unknown')

    def test_cycle_records_distinct_observation_and_preserves_first_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Config.load({'MEME_DATA':tmp,'MEME_SAFETY_ENABLED':'false'})
            store=Store(tmp);collector=Collector(spacing=0)
            with patch.object(collector,'collect',side_effect=lambda _:([dict(self.pair)],[])),patch('radar.Checker.check',return_value={'status':'safe','eligible':True}):
                first=cycle(cfg,store,collector);second=cycle(cfg,store,collector)
            self.assertEqual(first['post_research']['matched'],1)
            self.assertEqual(first['rows'][0]['first_seen_at'],second['rows'][0]['first_seen_at'])
            self.assertIn('非最后成交时间',fmt(second['rows'][0],second['rows'][0]['score_meta']))
            self.assertEqual(second['wallet_research']['status'],'no_evidence_source')
            with store.db() as db:
                rows=db.execute("SELECT payload FROM forward_tracks WHERE key LIKE 'early-microcap-observe-v1:%'").fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(json.loads(rows[0]['payload'])['kind'],'early_microcap_observation')


if __name__ == '__main__':
    unittest.main()
