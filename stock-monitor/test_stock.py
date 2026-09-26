import json, tempfile, unittest, time
from pathlib import Path
from unittest.mock import patch
from config import Config
from radar import cycle, grouped_rows, market_group
from scoring import score
from sources import parse_chart
from storage import Store

class TestStock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg = Config.load({'STOCK_DATA': self.tmp.name, 'STOCK_UNIVERSE': str(Path(self.tmp.name)/'universe.json')})
        self.store = Store(self.tmp.name)
    def chart(self):
        end = int(time.time()//300)*300-600
        yesterday = end-86400
        timestamps = [yesterday]+[end-(20-i)*300 for i in range(21)]
        return {'chart': {'result': [{'meta': {'currency': 'USD', 'exchangeTimezoneName':'UTC'}, 'timestamp': timestamps, 'indicators': {'quote': [{'close': [11]+[12]*21, 'volume': [100]*21+[400]}]}}]}}

    def test_parse_and_score(self):
        row = parse_chart(self.chart(), {'symbol':'NVDA','name':'NVIDIA','theme':'AI','region':'US','theme_weight':35})
        self.assertAlmostEqual(row['change'], 9.09, places=1); self.assertGreater(score(row)['score'], 35)
    def test_cycle_queues_ranking_once(self):
        from dataclasses import replace
        cfg = replace(self.cfg, telegram_enabled=True, telegram_token='1:x', telegram_chat='1')
        Path(self.cfg.universe).write_text(json.dumps([{'symbol':'NVDA','name':'NVIDIA','theme':'AI','region':'US','theme_weight':35}]))
        from sources import Collector
        c = Collector(spacing=0)
        with patch('sources.get_json', return_value=self.chart()):
            first = cycle(cfg, self.store, c)
            second = cycle(cfg, self.store, c)
        self.assertEqual(first['rows'][0]['symbol'], 'NVDA')
        self.assertEqual(second['events'], [])
        with self.store.db() as d: self.assertEqual(d.execute("select count(*) from outbox where state='pending'").fetchone()[0], 2)
    def test_stale_data_never_alerts(self):
        from dataclasses import replace
        from sources import Collector
        raw = self.chart()
        raw['chart']['result'][0]['timestamp'] = [t-86400*3 for t in raw['chart']['result'][0]['timestamp']]
        cfg = replace(self.cfg,telegram_enabled=True)
        with patch('sources.get_json',return_value=raw):
            report=cycle(cfg,self.store,Collector(spacing=0),[{'symbol':'NVDA','name':'N','theme':'AI','region':'US','theme_weight':35}])
        self.assertFalse(report['rows'][0]['fresh'])
        with self.store.db() as d:self.assertEqual(d.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
    def test_null_close_keeps_timestamp_alignment(self):
        raw=self.chart();r=raw['chart']['result'][0];r['indicators']['quote'][0]['close'][-1]=None
        parsed=parse_chart(raw,{'symbol':'NVDA'})
        self.assertEqual(parsed['quote_at'],r['timestamp'][-2]+300)
    def test_live_collector_requests_intraday(self):
        from sources import Collector
        with patch('sources.get_json',return_value=self.chart()) as get:
            Collector(spacing=0).collect([{'symbol':'NVDA'}])
        self.assertEqual(get.call_args.kwargs['params']['interval'],'5m')
    def test_credentials_disabled_by_default(self):
        self.assertFalse(self.cfg.telegram_enabled); self.assertFalse(self.cfg.dingtalk_enabled)
    def test_market_groups_are_separate(self):
        rows=[{'symbol':'000001.SZ','region':'中国A股'},{'symbol':'NVDA','region':'美国'},{'symbol':'ASML','region':'欧洲'}]
        groups=grouped_rows(rows)
        self.assertEqual([r['symbol'] for r in groups['中国股票']], ['000001.SZ'])
        self.assertEqual([r['symbol'] for r in groups['美国股票']], ['NVDA'])
        self.assertEqual([r['symbol'] for r in groups['其他市场']], ['ASML'])

if __name__ == '__main__': unittest.main()
