import unittest
from markets import market_group,grouped_rows

class Markets(unittest.TestCase):
    def test_listing_exchange_overrides_old_broad_group(self):
        self.assertEqual(market_group({'symbol':'600000.SS','market_group':'中国股票'}),'A股')
        self.assertEqual(market_group({'symbol':'0700.HK','market_group':'中国股票'}),'港股')
        self.assertEqual(market_group({'symbol':'BABA','region':'US'}),'美股')
        self.assertEqual(market_group({'symbol':'005930.KS'}),'其他市场')

    def test_hong_kong_cannot_displace_a_share_slots(self):
        rows=[{'symbol':f'{n:04}.HK','score_meta':{'score':90}} for n in range(8)]
        rows.append({'symbol':'600000.SS','score_meta':{'score':10}})
        groups=grouped_rows(rows)
        self.assertEqual(len(groups['A股'][:5]),1)
        self.assertEqual(len(groups['港股'][:5]),5)

if __name__=='__main__':unittest.main()
