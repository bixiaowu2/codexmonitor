import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import subprocess
from unittest.mock import patch

import radar_maintenance as m


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def db(self):
        path = self.root / 'radar.sqlite'
        with sqlite3.connect(path) as db:
            db.executescript('''CREATE TABLE runtime(key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE outbox(key TEXT PRIMARY KEY,created REAL,payload TEXT);
              CREATE TABLE dingtalk_outbox(key TEXT PRIMARY KEY,created REAL,payload TEXT);
              CREATE TRIGGER fanout AFTER INSERT ON outbox BEGIN
                INSERT INTO dingtalk_outbox VALUES(NEW.key,NEW.created,NEW.payload); END;''')
        return path

    def test_disk_thresholds(self):
        gib = 2**30
        self.assertFalse(m.disk_status(shutil._ntuple_diskusage(100*gib, 69*gib, 31*gib))['alert'])
        self.assertTrue(m.disk_status(shutil._ntuple_diskusage(100*gib, 80*gib, 20*gib))['alert'])
        self.assertTrue(m.disk_status(shutil._ntuple_diskusage(20*gib, 11*gib, 9*gib))['alert'])

    def test_incident_dedup_recovery_and_independent_channels(self):
        path = self.db()
        for now in [100, 200, 300]:
            m.alert(path, 'disk', True, 'low', now)
        m.alert(path, 'disk', True, 'still low', 100 + m.DAY)
        m.alert(path, 'disk', False, 'recovered', 200 + m.DAY)
        m.alert(path, 'disk', False, 'recovered', 300 + m.DAY)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT count(*) FROM dingtalk_outbox').fetchone()[0], 3)

    def test_rotation_preserves_foreign_files_and_symlinks(self):
        for i in range(1, 10):
            (self.root / f'alpha-daily-2026010{i}.sqlite').touch()
        protected = self.root / 'radar-before-lockfix.sqlite'
        protected.touch()
        link = self.root / 'alpha-daily-20250101.sqlite'
        link.symlink_to(protected)
        m.rotate(self.root, 'alpha-daily-', '.sqlite', 7)
        self.assertTrue(protected.exists())
        self.assertTrue(link.is_symlink())
        self.assertEqual(len([p for p in self.root.glob('alpha-daily-*') if not p.is_symlink()]), 7)

    def test_raw_cleanup_is_exact_allowlist_and_dry_run(self):
        raw = self.root / 'raw'
        raw.mkdir()
        old = raw / ('a'*64 + '.json')
        old.write_text('{}')
        os.utime(old, (0, 0))
        ledger = raw / 'forward_tracks.json'
        ledger.write_text('protected')
        os.utime(ledger, (0, 0))
        (raw / ('b'*64 + '.json')).symlink_to(ledger)
        self.assertEqual(m.prune_raw(raw, 40*m.DAY), 2)
        self.assertTrue(old.exists())
        m.prune_raw(raw, 40*m.DAY, True)
        self.assertFalse(old.exists())
        self.assertTrue(ledger.exists())

    def test_online_backup_keeps_ledger_and_permissions(self):
        source = self.db()
        with sqlite3.connect(source) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("INSERT INTO runtime VALUES('ledger','123')")
        target = self.root / 'backup.sqlite'
        m.backup_sqlite(source, target)
        with m.read_db(target) as db:
            self.assertEqual(db.execute('SELECT value FROM runtime').fetchone()[0], '123')
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.root.glob('backup.tmp*')))

    def test_low_disk_skips_backup_and_does_not_delete(self):
        source = self.db()
        usage = shutil._ntuple_diskusage(100, 90, 10)
        with patch('radar_maintenance.shutil.disk_usage', return_value=usage):
            with self.assertRaises(OSError):
                m.backups(self.root, {'alpha': source}, self.root/'missing', 100)
        self.assertTrue(source.exists())

    def test_late_endpoints_excluded_and_report_does_not_write(self):
        source = self.root / 'forward.sqlite'
        with sqlite3.connect(source) as db:
            db.execute('''CREATE TABLE forward_tracks(market TEXT, created REAL, entry REAL,
              peak REAL, max_drawdown REAL, marks TEXT, payload TEXT)''')
            marks = {'1h': {'return': 2, 'observed_at': 10000}}
            db.execute('INSERT INTO forward_tracks VALUES(?,?,?,?,?,?,?)',
                       ('bsc', 1, 1, 3, .2, json.dumps(marks), '{}'))
        before = source.read_bytes()
        with m.read_db(source) as db:
            result = m.outcomes(db, 'forward_tracks', 20000)
        horizon = result['groups'][0]['endpoints']['1h']
        self.assertEqual(horizon['valid'], 0)
        self.assertEqual(horizon['missing'], 1)
        self.assertEqual(horizon['excluded'], 1)
        self.assertEqual(source.read_bytes(), before)

    def test_backup_ring_includes_x_pending_state(self):
        source = self.db()
        state = self.root / 'state.json'
        state.write_text(json.dumps({'seen': {}, 'pending': {'post': {'remaining': ['opaque']}}}))
        usage = shutil._ntuple_diskusage(100*2**30, 10*2**30, 90*2**30)
        with patch('radar_maintenance.shutil.disk_usage', return_value=usage):
            for day in range(10):
                m.backups(self.root, {'alpha': source}, state, (20000+day)*m.DAY)
        self.assertEqual(len(list((self.root/'backups').glob('alpha-daily-*'))), 7)
        saved = json.loads(sorted((self.root/'backups').glob('x-daily-*'))[-1].read_text())
        self.assertIn('post', saved['pending'])

    def test_x_report_excludes_inactive_accounts_and_flags_missing(self):
        state = self.root / 'state.json'
        config = self.root / 'accounts.json'
        config.write_text('["binancezh", "binancewallet"]')
        state.write_text(json.dumps({'health': {'heartbeat': 99, 'accounts': {
            'binancezh': {'last_success': 90, 'failures': 0},
            'old': {'last_success': 1, 'failures': 99}}}, 'pending': {}}))
        result = subprocess.CompletedProcess([], 0, 'active\n', '')
        with patch.object(m, 'X_ACCOUNTS', config), patch('radar_maintenance.subprocess.run', return_value=result):
            report = m.make_report({}, state, 100)['radars']['x']
        self.assertEqual(set(report['accounts']), {'binancezh'})
        self.assertEqual(report['missing_account_health'], ['binancewallet'])


if __name__ == '__main__':
    unittest.main()
