import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from config import Config
from radar import cycle
from scoring import score
from sources import parse_chart
from storage import Store

class TestStock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg = Config.load({'STOCK_DATA': self.tmp.name, 'STOCK_UNIVERSE': str(Path(self.tmp.name)/'universe.json')})
        self.store = Store(self.tmp.name)
    def chart(self):
        return {'chart': {'result': [{'meta': {'currency': 'USD', 'marketState': 'REGULAR'}, 'timestamp': [1,2,3], 'indicators': {'quote': [{'close': [10, 11, 12], 'volume': [100, 110, 400]}]}}]}}
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
    def test_credentials_disabled_by_default(self):
        self.assertFalse(self.cfg.telegram_enabled); self.assertFalse(self.cfg.dingtalk_enabled)

if __name__ == '__main__': unittest.main()
