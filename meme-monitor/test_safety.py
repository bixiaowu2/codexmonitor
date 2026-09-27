import copy
import tempfile
import time
import unittest
from dataclasses import replace
from unittest.mock import patch
from config import Config
from net import FetchError
from radar import cycle, enqueue_events, safety_text
from safety import Checker, assess, top10, EVM_CHAIN_IDS
from scoring import score
from sources import Collector
from storage import Store

ADDRESS = '0x' + 'a'*40
SOL_ADDRESS = 'So11111111111111111111111111111111111111112'


def evm(**kwargs):
    result = {key: '0' for key in ('is_honeypot','cannot_sell_all','cannot_buy',
              'transfer_pausable','is_blacklisted','owner_change_balance','is_proxy',
              'is_mintable','slippage_modifiable','personal_slippage_modifiable','buy_tax','sell_tax',
              'selfdestruct','can_take_back_ownership','hidden_owner')}
    result.update(is_open_source='1', holders=[{'address': 'holder', 'percent': '.01'}])
    result.update(kwargs)
    return result


def sol(**kwargs):
    result = {key: {'status': '0'} for key in ('freezable','closable','balance_mutable_authority',
              'mintable','transfer_fee_upgradable','transfer_hook_upgradable','default_account_state_upgradable')}
    result.update(non_transferable='0', default_account_state='1', transfer_hook=[], transfer_fee={}, holders=[{'account': 'holder','percent': '.01'}])
    result.update(kwargs)
    return result


def pair(address=ADDRESS, **kwargs):
    result = {'chain':'bsc','address':address,'pair_address':'0xpool','symbol':'TEST',
              'price_usd':1,'liquidity_usd':100000,'volume_1h':400000,'buys_1h':100,
              'sells_1h':0,'change_1h':30,'created_at':time.time()-60}
    result.update(kwargs)
    return result


def response(result, address=ADDRESS):
    return {'code':1,'result':{address:result}}


class TestSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config.load({'MEME_DATA':self.tmp.name})
        self.store = Store(self.tmp.name)

    def run_cycle(self, rows, result=None, cfg=None):
        collector = Collector(spacing=0)
        with patch.object(collector,'collect',return_value=(copy.deepcopy(rows),[])):
            if result is None:
                return cycle(cfg or self.cfg,self.store,collector)
            with patch('safety.get_json', return_value=response(result)):
                return cycle(cfg or self.cfg,self.store,collector)

    def test_defaults_enforce_safety(self):
        self.assertTrue(self.cfg.safety_enabled)

    def test_evm_hard_blocks(self):
        for key in ('is_honeypot','cannot_sell_all','cannot_buy','transfer_pausable','is_blacklisted','owner_change_balance','selfdestruct'):
            with self.subTest(key=key):
                self.assertEqual(assess('bsc',evm(**{key:'1'}))['status'],'blocked')

    def test_solana_hard_blocks(self):
        for key in ('freezable','closable','balance_mutable_authority','non_transferable'):
            with self.subTest(key=key):
                self.assertEqual(assess('solana',sol(**{key:{'status':'1'}}))['status'],'blocked')
        self.assertEqual(assess('solana',sol(default_account_state='2'))['status'],'blocked')

    def test_partial_and_malformed_never_pass(self):
        for chain, result in [('bsc',{}),('bsc',{'token_name':'Test'}),('solana',{}),('bsc',evm(sell_tax='NaN')),('bsc',evm(sell_tax='20')),('bsc',evm(is_honeypot='')),('solana',sol(freezable={}))]:
            with self.subTest(chain=chain,result=result):
                self.assertEqual(assess(chain,result)['status'],'unknown')

    def test_complete_negative_result_is_not_sell_simulation(self):
        for chain, result in [('bsc',evm()),('solana',sol())]:
            checked=assess(chain,result)
            self.assertEqual(checked['status'],'safe')
            self.assertTrue(checked['eligible'])
            self.assertFalse(checked['sell_simulated'])

    def test_block_even_when_other_fields_missing(self):
        self.assertEqual(assess('bsc',{'is_honeypot':'1'})['status'],'blocked')

    def test_tax_boundaries(self):
        self.assertEqual(assess('bsc',evm(sell_tax='.20'))['status'],'blocked')
        self.assertFalse(assess('bsc',evm(sell_tax='.10'))['eligible'])
        self.assertTrue(assess('bsc',evm(sell_tax='.09'))['eligible'])

    def test_holder_concentration_and_known_pool_exclusion(self):
        self.assertEqual(assess('bsc',evm(holders=[{'percent':'.91'}]))['status'],'blocked')
        self.assertFalse(assess('bsc',evm(holders=[{'percent':'.5'}]))['eligible'])
        self.assertAlmostEqual(top10({'holders':[{'percent':'.95','tag':'LP'},{'percent':'.01'}]}),.01)
        self.assertIsNone(top10({'holders':[{'percent':'91'}]}))
        self.assertIsNone(top10({}))
        self.assertFalse(assess('bsc',evm(holders=[]))['eligible'])

    def test_privileges_warn_and_prevent_positive_events(self):
        for key in ('is_mintable','is_proxy','hidden_owner','slippage_modifiable'):
            checked=assess('bsc',evm(**{key:'1'}))
            self.assertFalse(checked['eligible'])
            self.assertTrue(checked['warnings'])
        self.assertFalse(assess('solana',sol(transfer_hook=[{'program_id':'x'}]))['eligible'])
        self.assertFalse(assess('solana',sol(transfer_fee={'configured':True}))['eligible'])

    def test_cache_identity_and_hits(self):
        checker=Checker(True)
        with patch('safety.get_json',return_value=response(evm())) as get:
            first=checker.check(pair());second=checker.check(pair(address='0x'+'A'*40))
        self.assertEqual(first,second)
        self.assertEqual(get.call_count,1)
        self.assertEqual(checker.cache_hits,1)
        restored=Checker(True,cache=checker.snapshot())
        with patch('safety.get_json') as get:
            self.assertEqual(restored.check(pair())['status'],'safe')
            get.assert_not_called()

    def test_expired_cache_not_used_as_safe_when_budget_exhausted(self):
        checker=Checker(True)
        with patch('safety.get_json',return_value=response(evm())):
            checker.check(pair())
        checker.cache['bsc:'+ADDRESS]['checked_at']-=901
        exhausted=Checker(True,cache=checker.snapshot(),max_checks=0)
        self.assertEqual(exhausted.check(pair())['status'],'unknown')

    def test_api_failure_is_unknown_and_backoff_persists(self):
        checker=Checker(True)
        with patch('safety.get_json',side_effect=FetchError('http_429')) as get:
            self.assertEqual(checker.check(pair())['status'],'unknown')
            self.assertEqual(checker.check(pair(address='0x'+'b'*40))['provider'],'backoff')
            self.assertEqual(get.call_count,1)
        restored=Checker(True,cache=checker.snapshot())
        with patch('safety.get_json') as get:
            self.assertEqual(restored.check(pair(address='0x'+'c'*40))['provider'],'backoff')
            get.assert_not_called()

    def test_wrong_identity_and_api_rejection(self):
        for raw in [{'code':0,'result':{ADDRESS:evm()}},response(evm(),'0x'+'b'*40),{'code':1,'result':[]}]:
            with patch('safety.get_json',return_value=raw):
                self.assertEqual(Checker(True).check(pair())['status'],'unknown')
        with patch('safety.get_json',return_value=response(sol(),SOL_ADDRESS.lower())):
            self.assertEqual(Checker(True).check(pair(SOL_ADDRESS,chain='solana'))['status'],'unknown')

    def test_chain_ids_verified_and_invalid_address_no_request(self):
        self.assertEqual(EVM_CHAIN_IDS['robinhood'],'4663')
        for chain, chain_id in EVM_CHAIN_IDS.items():
            with patch('safety.get_json',return_value=response(evm())) as get:
                Checker(True).check(pair(chain=chain))
                self.assertIn('/token_security/'+chain_id+'?',get.call_args.args[0])
        with patch('safety.get_json') as get:
            self.assertEqual(Checker(True).check(pair('evil?query'))['status'],'unknown')
            self.assertEqual(Checker(True).check(pair(chain='unsupported'))['status'],'unknown')
            get.assert_not_called()

    def test_request_budget_counts_misses_only(self):
        checker=Checker(True,max_checks=1)
        with patch('safety.get_json',return_value=response(evm())) as get:
            self.assertEqual(checker.check(pair())['status'],'safe')
            self.assertEqual(checker.check(pair())['status'],'safe')
            self.assertEqual(checker.check(pair('0x'+'b'*40))['provider'],'budget')
            self.assertEqual(get.call_count,1)
        with patch('safety.get_json') as get:
            self.assertEqual(Checker(True,budget_seconds=0).check(pair())['provider'],'budget')
            get.assert_not_called()

    def test_disabled_and_unknown_cannot_send_positive_events(self):
        for status in ('unknown','disabled','blocked','safe'):
            p=pair(safety_status=status,safety_eligible=False)
            p['score_meta']={'score':100,'risk':[]}
            for old in ({},{'bsc:'+ADDRESS:{'score':20}}):
                self.assertFalse(any(e['kind'] in ('new_hot','surge') for e in enqueue_events(self.cfg,self.store,[p],old,time.time())))
        with patch('safety.get_json') as get:
            report=self.run_cycle([pair()],cfg=replace(self.cfg,safety_enabled=False))
            self.assertEqual(report['events'],[])
            self.assertEqual(report['safety']['disabled'],1)
            get.assert_not_called()

    def test_unknown_demoted_and_reported(self):
        with patch('safety.get_json',side_effect=FetchError('timeout')):
            report=self.run_cycle([pair()])
        self.assertEqual(report['events'],[])
        self.assertEqual(report['safety']['unknown'],1)
        self.assertEqual(report['safety']['errors'],1)
        self.assertTrue(any(e.startswith('safety:') for e in report['errors']))
        self.assertLess(report['rows'][0]['score_meta']['score'],score(pair())['score'])
        self.assertEqual(self.store.state('latest')['safety'],report['safety'])

    def test_blocked_removed_but_audited(self):
        report=self.run_cycle([pair()],evm(is_honeypot='1'))
        self.assertEqual(report['candidates'],0)
        self.assertEqual(report['safety']['blocked'],1)
        self.assertEqual(len(report['blocked_rows']),1)
        with self.store.db() as d:
            self.assertEqual(d.execute('SELECT COUNT(*) FROM signals').fetchone()[0],1)

    def test_good_to_bad_sends_risk_cancels_pending_and_observes_track(self):
        cfg=replace(self.cfg,telegram_enabled=True,telegram_token='1:x',telegram_chat='1')
        first=self.run_cycle([pair()],evm(),cfg)
        self.assertEqual(first['events'][0]['kind'],'new_hot')
        self.store.put('safety_cache',{})
        second=self.run_cycle([pair(price_usd=.5)],evm(is_honeypot='1'),cfg)
        self.assertEqual([e['kind'] for e in second['events']],['risk'])
        self.assertEqual(second['candidates'],0)
        third=self.run_cycle([pair(price_usd=.5)],cfg=cfg)
        self.assertEqual(third['events'],[])
        with self.store.db() as d:
            self.assertEqual(d.execute("SELECT COUNT(*) FROM outbox WHERE state='expired'").fetchone()[0],2)
            self.assertEqual(d.execute("SELECT COUNT(*) FROM outbox WHERE state='pending'").fetchone()[0],1)
            self.assertIn('蜜罐',d.execute("SELECT payload FROM outbox WHERE state='pending'").fetchone()[0])
            self.assertEqual(d.execute('SELECT COUNT(*) FROM forward_tracks').fetchone()[0],2)

    def test_risk_precedes_surge_and_format_has_timestamp(self):
        p=pair(safety_status='unknown',safety_eligible=False,safety_checked_at=time.time(),safety_warnings=['接口失败'])
        p['score_meta']={'score':100}
        old={'bsc:'+ADDRESS:{'score':1,'safety_eligible':True}}
        self.assertEqual(enqueue_events(self.cfg,self.store,[p],old,time.time())[0]['kind'],'risk')
        self.assertIn('GoPlus',safety_text(p))
        self.assertIn('仅观察',safety_text(p))

    def test_cache_budget_does_not_starve_new_candidates(self):
        self.run_cycle([pair()],evm())
        other='0x'+'b'*40
        with patch('safety.get_json',return_value=response(evm(),other)) as get:
            report=self.run_cycle([pair(),pair(other)],cfg=replace(self.cfg,safety_max_checks=1))
        self.assertEqual(report['safety']['cache_hits'],1)
        self.assertEqual(report['safety']['requests'],1)
        self.assertEqual(report['safety']['safe'],2)
        self.assertEqual(get.call_count,1)


if __name__=='__main__': unittest.main()
