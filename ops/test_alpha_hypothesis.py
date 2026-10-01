import sqlite3
import unittest
import alpha_hypothesis as h
import holdings_core as c

NOW=1790862000.


def fixture():
    row={'address':'0x'+'a'*40,'symbol':'AAA','futures_symbol':'AAAUSDT','change24h':10,
         'history_features':{'return_7d':.2},'history_candle_end':int(NOW*1000)//86400000*86400000-1}
    observation={'dual_matched':True,'features':{'return_1h':.01},'quote':{'price':1,'time':NOW*1000},
                 'safety':{'status':'no_listed_risk_detected','observed':NOW,'raw_top10_share':.95}}
    oi={'symbol':'AAAUSDT','openInterest':'3000000','time':NOW*1000}
    mark={'symbol':'AAAUSDT','markPrice':'2','time':NOW*1000}
    return row,observation,set(),oi,mark,NOW


class Hypothesis(unittest.TestCase):
    def test_oi_is_notional_not_contract_quantity_or_volume(self):
        args=fixture();result=h.assess(*args)
        self.assertTrue(result['matched']);self.assertEqual(result['open_interest_usd'],6000000)
        self.assertEqual(result['control_inference'],'not_established')
        args[3]['openInterest']='2000000'
        self.assertFalse(h.assess(*args)['matched'])

    def test_spot_unknown_existing_identity_and_stale_oi_excluded(self):
        args=list(fixture());args[2]=None
        self.assertFalse(h.assess(*args)['matched'])
        args[2]={'AAA'};self.assertTrue(h.assess(*args)['excluded'])
        args[2]=set();args[3]['time']=(NOW-181)*1000
        self.assertFalse(h.assess(*args)['matched'])
        args[3]['time']=NOW*1000;args[3]['symbol']='OTHER'
        self.assertFalse(h.assess(*args)['matched'])

    def test_missing_cleaned_concentration_never_establishes_control(self):
        args=fixture();args[1]['safety']['raw_top10_share']=None
        result=h.assess(*args);self.assertFalse(result['matched']);self.assertTrue(result['unknown'])
        args[1]['safety']['raw_top10_share']=.95;args[0]['history_features']=None
        self.assertFalse(h.assess(*args)['matched'])

    def test_overheated_and_unsafe_assets_cannot_join_cohort(self):
        for field,value in [('change24h',51),('history_features',{'return_7d':1.1})]:
            args=fixture();args[0][field]=value
            self.assertFalse(h.assess(*args)['matched'])
        args=fixture();args[1]['safety']['flags']=['mintable']
        self.assertFalse(h.assess(*args)['matched'])

    def test_forward_endpoint_missing_is_not_peak_return(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);c.initialize(db);h.initialize(db)
        result=h.assess(*fixture())
        with db:h.record(db,[result],NOW);h.record(db,[result],NOW)
        self.assertEqual(db.execute('SELECT count(*) FROM hypothesis_tracks').fetchone()[0],1)
        with db:h.observe(db,[{'address':result['address'],'quote':{'price':2,'time':(NOW+3600)*1000}}],NOW+3600)
        row=db.execute('SELECT * FROM hypothesis_tracks').fetchone()
        import json
        self.assertEqual(json.loads(row['marks'])['3600']['multiple'],2)
        with db:h.observe(db,[],NOW+21600+1801)
        marks=json.loads(db.execute('SELECT marks FROM hypothesis_tracks').fetchone()[0])
        self.assertTrue(marks['21600']['missing'])


if __name__=='__main__':unittest.main()
