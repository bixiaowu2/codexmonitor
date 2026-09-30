import tempfile
import unittest
from storage import Store


class RetentionTests(unittest.TestCase):
    def test_old_signal_and_forward_ledger_survive_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            with store.db() as db:
                db.execute("INSERT INTO signals VALUES('old',1,'{}')")
                db.execute("INSERT INTO forward_tracks(key,instrument_key,symbol,market,created,entry,last,peak,trough,checked,payload) VALUES('old','x','X','bsc',1,1,1,1,1,1,'{}')")
            store.cleanup(now=200*86400)
            with store.db() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM signals').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT count(*) FROM forward_tracks').fetchone()[0], 1)
