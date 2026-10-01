import sqlite3
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
import daily_brief as d
import holdings_core as c
import holdings_service as s


def at(value, zone='Asia/Shanghai'):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo(zone)).timestamp()


class DailyBrief(unittest.TestCase):
    def test_beijing_and_new_york_slots_and_no_late_catchup(self):
        self.assertEqual([v[0] for v in d.slots(at('2026-10-02T09:00'))],['alpha','meme'])
        self.assertEqual([v[1] for v in d.slots(at('2026-10-02T10:00'))],['A股','港股'])
        self.assertEqual([v[1] for v in d.slots(at('2026-10-02T10:00','America/New_York'))],['美股'])
        self.assertEqual(d.slots(at('2026-10-02T12:00')),[])
        self.assertEqual(d.slots(at('2026-10-03T10:00')),[])

    def test_old_quotes_cannot_be_current_market_direction(self):
        now=at('2026-10-02T10:00')
        report={'as_of':now,'markets':{'A股':[{'fresh':True,'quote_at':now-901,'change':99}], '美股':[{'fresh':True,'quote_at':now,'change':100}]}}
        text=d.render('stock','A股',report,now,'testbot')
        self.assertIn('方向未知',text)
        self.assertNotIn('多数上涨',text)
        self.assertIn('https://t.me/testbot',text)

    def test_daily_both_channels_once_and_appropriate_registration(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);c.initialize(db)
        routes={name:{'telegram':True,'dingtalk':True} for name in ('alpha','meme','stock')}
        now=at('2026-10-02T09:00')
        d.queue_daily(db,routes,now,s.enqueue,lambda _: {})
        d.queue_daily(db,routes,now+60,s.enqueue,lambda _: {})
        rows=db.execute('SELECT * FROM outbox').fetchall()
        self.assertEqual(len(rows),4)
        self.assertEqual({r['channel'] for r in rows},{'telegram','dingtalk'})
        for row in rows:
            self.assertIn('快照缺失或过期',row['text'])
            self.assertIn('实际持仓登记提示',row['text'])
            self.assertIn('/buy',row['text'])
            self.assertIn('钉钉不接收',row['text'])


if __name__=='__main__':unittest.main()
