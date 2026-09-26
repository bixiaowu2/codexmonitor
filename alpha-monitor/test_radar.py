import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import radar as r


def bars(n=140):
    return [{'t':i*r.DAY,'end':(i+1)*r.DAY-1,'o':1.,'h':1.1,'l':.9,'c':1.,'q':100000.,'n':100} for i in range(n)]

class IdentityTests(unittest.TestCase):
    def f(self,base='TEST'):
        return {'symbol':base+'USDT','baseAsset':base,'quoteAsset':'USDT','contractType':'PERPETUAL','status':'TRADING','onboardDate':0}
    def t(self,symbol='TEST',address='0xa',chain='56',alias=''):
        return {'alphaId':'ALPHA_1','symbol':symbol,'contractAddress':address,'chainId':chain,'cexCoinName':alias,'listingTime':0}
    def test_chain_status_and_ambiguous_contract(self):
        a,issues=r.universe([self.t(),self.t(address='0xb'),self.t(chain='1')],{'symbols':[self.f()]})
        self.assertFalse(a);self.assertEqual(len(issues),1)
        a,_=r.universe([self.t(),dict(self.t(address='0xb'),offline=True)],{'symbols':[self.f()]})
        self.assertEqual(len(a),1)
    def test_numeric_multiplier_is_not_guessed(self):
        a,_=r.universe([self.t('CHEEMS')],{'symbols':[self.f('1000CHEEMS')]});self.assertFalse(a)
        a,_=r.universe([self.t('CHEEMS',alias='1000CHEEMS')],{'symbols':[self.f('1000CHEEMS')]});self.assertEqual(len(a),1)
    def test_multiple_contracts_for_same_base_are_excluded(self):
        f=self.f();g=dict(f,symbol='TEST_2USDT')
        self.assertFalse(r.universe([self.t()],{'symbols':[f,g]})[0])

class HistoryTests(unittest.TestCase):
    def test_no_global_min_max_time_reversal(self):
        b=bars(4);b[0].update(o=10,h=10,l=10,c=10);b[-1].update(o=1,h=1,l=1,c=1)
        out=r.outcomes(b,0)
        self.assertEqual(out['peak_multiple'],1)
        self.assertLess(out['chronological_low_close_to_later_high'],2)
    def test_both_listing_anchor_prevents_prelisting_gain(self):
        b=bars();b[0]['h']=1000
        self.assertAlmostEqual(r.outcomes(b,2*r.DAY)['peak_multiple'],1.1)
    def test_closed_candles_only_and_invalid_missing_quotes(self):
        raw=[[0,'1','2','.5','1.5','100',r.DAY-1,'100','2'],[r.DAY,'1','2','.5','1','100',2*r.DAY-1,'100','2']]
        self.assertEqual(len(r.candles(raw,r.DAY)),1)
        raw[0][7]='NaN';self.assertEqual(len(r.candles(raw,r.DAY)),0)
    def test_feature_looks_back_only(self):
        b=bars();before=r.features_at(b,30);b[35].update(c=10000,q=1e20)
        self.assertEqual(before,r.features_at(b,30))
        b[30].update(c=1.2,h=1.2,q=300000)
        self.assertTrue(r.early_signal(r.features_at(b,30)))
    def test_incomplete_horizon_not_labelled_failure(self):
        self.assertFalse(r.outcomes(bars(89),0)['90d_complete'])
        self.assertIsNone(r.outcomes(bars(89),0)['90d_peak_multiple'])
        self.assertTrue(r.outcomes(bars(90),0)['90d_complete'])
        b=bars(91);del b[50];self.assertFalse(r.outcomes(b,0)['90d_complete'])
    def test_missing_day_invalidates_features(self):
        b=bars();del b[15];self.assertIsNone(r.features_at(b,30))
    def test_entry_candle_low_high_order_not_assumed(self):
        b=bars(2);b[0].update(o=100,h=110,l=.01,c=100);b[1].update(o=100,h=110,l=99,c=100)
        self.assertAlmostEqual(r.outcomes(b,0)['chronological_low_close_to_later_high'],1.1)

class ScoringTests(unittest.TestCase):
    def test_missing_not_safe(self):
        s=r.snapshot_score({},None,None);self.assertTrue(s['blocks']);self.assertEqual(s['screen_score'],0)
        self.assertIsNone(s['market_cap']);self.assertIsNone(s['funding_rate'])
    def test_100x_cap_and_unit_mismatch_gate(self):
        t={'marketCap':2e8,'fdv':3e8,'liquidity':1e6,'volume24h':1e6,'price':1,'percentChange24h':2}
        s=r.snapshot_score(t,{'quoteVolume':1e8,'lastPrice':1000},{'lastFundingRate':0})
        self.assertEqual(s['implied_100x_market_cap'],2e10)
        self.assertTrue(any('身份' in x for x in s['blocks']))
        self.assertTrue(any('100亿美元' in x for x in s['blocks']))
    def test_api_list_response_and_schema_error(self):
        class P:
            returncode=0;stdout=b'[{"symbol":"XUSDT"}]';stderr=b''
        with tempfile.TemporaryDirectory() as d,patch('radar.subprocess.run',return_value=P()):
            self.assertIsInstance(r.PublicAPI(d).data('https://example.com'),list)
            raw=list(Path(d).glob('*.json'))[0];self.assertIn('sha256',json.loads(raw.read_text()))
        with self.assertRaises(ValueError):r.universe({}, {})


class IntegrationTests(unittest.TestCase):
    def test_live_pipeline_persistence_and_event_dedup(self):
        import argparse, sqlite3, time
        now=int(time.time()*1000);start=(now//r.DAY-40)*r.DAY
        raw=[[start+i*r.DAY,'1','1.1','.9','1','100',(start+(i+1)*r.DAY)-1,'100000','100'] for i in range(40)]
        raw[-1][2]='1.25';raw[-1][4]='1.2';raw[-1][7]='300000'
        token={'chainId':'56','contractAddress':'0xabc','symbol':'TEST','alphaId':'ALPHA_1','name':'Test','listingTime':start,
               'marketCap':1e7,'fdv':2e7,'liquidity':1e6,'volume24h':2e6,'price':1.2,'percentChange24h':4,'denomination':1}
        future={'symbol':'TESTUSDT','baseAsset':'TEST','quoteAsset':'USDT','contractType':'PERPETUAL','status':'TRADING','onboardDate':start}
        def fake(_,url,params=None):
            if url==r.TOKENS:return [token]
            if url.endswith('exchangeInfo'):return {'symbols':[future]}
            if url.endswith('ticker/24hr'):return [{'symbol':'TESTUSDT','lastPrice':1.2,'quoteVolume':1e7,'closeTime':now}]
            if url.endswith('premiumIndex'):return [{'symbol':'TESTUSDT','lastFundingRate':0,'time':now}]
            if url.endswith('klines'):return raw
            if url.endswith('openInterest'):return {'openInterest':'100','time':now}
            if 'dexscreener' in url:return []
            raise AssertionError(url)
        with tempfile.TemporaryDirectory() as d,patch.object(r.PublicAPI,'data',fake):
            args=argparse.Namespace(data=d,enrich=1)
            a=r.scan(args);self.assertEqual(a['count'],1);self.assertTrue(a['candidates'][0]['watch_candidate'])
            r.scan(args)
            with sqlite3.connect(Path(d)/'radar.sqlite') as db:self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)
            before=(Path(d)/'latest.json').read_text()
            with patch.object(r.PublicAPI,'data',side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError):r.scan(args)
            self.assertEqual(before,(Path(d)/'latest.json').read_text())
            # No enrichment must not silently create a breakout candidate.
            args.enrich=0;self.assertTrue(r.scan(args)['candidates'][0]['watch_candidate'])
            # Fresh saved daily features are reusable; a new folder has no implied signal.
            with tempfile.TemporaryDirectory() as newdir:
                args.data=newdir;self.assertFalse(r.scan(args)['candidates'][0]['watch_candidate'])
    def test_futures_fallback_does_not_fabricate_alpha_price(self):
        raw=[[i*r.DAY,1,1.1,.9,1,100,(i+1)*r.DAY-1,100000,100] for i in range(150)]
        class API:
            def data(self,url,params=None):
                if url.startswith(r.ALPHA):raise RuntimeError('No records found')
                return raw
        p={'token':{'listingTime':0,'alphaId':'ALPHA_1','symbol':'X','name':'X','contractAddress':'0xa'},
           'future':{'symbol':'XUSDT','onboardDate':r.DAY}}
        x=r.study_one(API(),p)
        self.assertEqual(x['price_market'],'futures_proxy');self.assertIn('error',x['after_alpha'])
        self.assertIn('peak_multiple',x['after_both'])

class RotationTests(unittest.TestCase):
    def test_unchecked_lower_score_is_not_starved(self):
        old={'blocks':[],'history_checked_at':1000,'screen_score':70}
        new={'blocks':[],'history_checked_at':0,'screen_score':60}
        blocked={'blocks':['bad price'],'history_checked_at':0,'screen_score':100}
        self.assertEqual(r.select_enrichment([old,blocked,new],1),[new])
    def test_empty_primary_response_rejected(self):
        with self.assertRaises(ValueError):r.universe([],{'symbols':[]})

if __name__=="__main__":unittest.main()
