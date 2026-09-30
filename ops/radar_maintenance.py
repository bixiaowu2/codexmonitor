#!/usr/bin/env python3
"""Local maintenance and compact review evidence. No model or credential access."""
import argparse
import collections
import contextlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import subprocess
import time
from datetime import datetime, timezone

DATABASES = {
    'alpha': Path('/var/lib/alpha-radar/radar.sqlite'),
    'meme': Path('/var/lib/meme-radar/meme-radar.sqlite'),
    'stock': Path('/var/lib/stock-radar/stock-radar.sqlite'),
}
X_STATE = Path('/home/ubuntu/x-monitor-validation-20260926/x-monitor-no-api/data/state.json')
X_ACCOUNTS = Path('/etc/radar-maintenance-x-accounts.json')
HOME = Path('/var/lib/radar-maintenance')
SERVICES = ('alpha-radar', 'meme-radar', 'stock-radar', 'x-monitor')
DAY = 86400


@contextlib.contextmanager
def read_db(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('database_missing_or_symlink')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def disk_status(usage):
    percent = 100 * usage.used / usage.total
    return {'used_percent': round(percent, 1), 'free_gib': round(usage.free / 2**30, 2),
            'alert': percent >= 80 or usage.free < 10 * 2**30}


def alert(path, condition, active, text, now):
    """Persist incident and fan-out message in one transaction, repeat at most daily."""
    for attempt in range(4):
        try:
            return alert_once(path, condition, active, text, now)
        except sqlite3.OperationalError as exc:
            if 'locked' not in str(exc).lower() or attempt == 3:
                raise
            time.sleep(1)


def alert_once(path, condition, active, text, now):
    with sqlite3.connect(path, timeout=15) as db:
        key = 'maintenance:' + condition
        row = db.execute('SELECT value FROM runtime WHERE key=?', (key,)).fetchone()
        old = json.loads(row[0]) if row else {}
        previous = old.get('active', False)
        if not ((active and (not previous or now - old.get('notified', 0) >= DAY)) or (previous and not active)):
            return
        if (active and (not previous or now - old.get('notified', 0) >= DAY)) or (previous and not active):
            message_key = key + ':' + str(int(now))
            body = {'kind': 'ops', 'text': text}
            db.execute('INSERT OR IGNORE INTO outbox(key,created,payload) VALUES(?,?,?)',
                       (message_key, now, json.dumps(body, ensure_ascii=False)))
            old['notified'] = now
        old['active'] = active
        db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)', (key, json.dumps(old)))


def backup_sqlite(source, target):
    started = time.monotonic()
    def progress(*_):
        if time.monotonic() - started > 90:
            raise TimeoutError('backup_deadline')
    temp = target.with_suffix('.tmp')
    try:
        with read_db(source) as src, contextlib.closing(sqlite3.connect(temp)) as dst:
            os.chmod(temp, 0o600)
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
            dst.execute('PRAGMA journal_mode=DELETE')
            if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('backup_integrity')
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def rotate(folder, prefix, suffix, keep):
    pattern = re.compile(re.escape(prefix) + r'\d{8}' + re.escape(suffix))
    files = sorted((p for p in folder.iterdir() if pattern.fullmatch(p.name)
                    and p.is_file() and not p.is_symlink()), reverse=True)
    for path in files[keep:]:
        path.unlink()


def backups(root, databases, x_state, now):
    folder = root / 'backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%d')
    # Weekly snapshot is the Monday starting the current UTC week.
    week = datetime.fromtimestamp(now - datetime.fromtimestamp(now, timezone.utc).weekday() * DAY,
                                  timezone.utc).strftime('%Y%m%d')
    needed = sum(p.stat().st_size for p in databases.values()) * 2 + 512 * 2**20
    if shutil.disk_usage(folder).free - needed < 10 * 2**30:
        raise OSError('backup_skipped_keep_10gib_free')
    for name, source in databases.items():
        daily = folder / f'{name}-daily-{stamp}.sqlite'
        if not daily.exists():
            backup_sqlite(source, daily)
        weekly = folder / f'{name}-weekly-{week}.sqlite'
        if not weekly.exists():
            shutil.copyfile(daily, weekly)
            os.chmod(weekly, 0o600)
        rotate(folder, name + '-daily-', '.sqlite', 7)
        rotate(folder, name + '-weekly-', '.sqlite', 4)
    # X writes state with atomic rename; one read gives a coherent snapshot.
    value = json.loads(x_state.read_text())
    if not isinstance(value.get('seen'), dict) or not isinstance(value.get('pending'), dict):
        raise ValueError('x_state_structure')
    for kind, date in [('daily', stamp), ('weekly', week)]:
        target = folder / f'x-{kind}-{date}.json'
        if not target.exists():
            atomic_json(target, value)
        rotate(folder, f'x-{kind}-', '.json', 7 if kind == 'daily' else 4)


def prune_raw(folder, now, apply=False):
    removed = 0
    if folder.is_symlink() or not folder.is_dir():
        return removed
    for path in folder.iterdir():
        if (re.fullmatch(r'[0-9a-f]{64}\.json', path.name) and not path.is_symlink()
                and path.is_file() and path.stat().st_mtime < now - 30 * DAY):
            removed += path.stat().st_size
            if apply:
                path.unlink()
    return removed


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def outcomes(db, table, now):
    groups = collections.defaultdict(list)
    if table not in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        return {'unavailable': True}
    alpha = table == 'evaluation_tracks'
    for row in db.execute('SELECT * FROM ' + table):
        r = dict(row)
        saved = json.loads(r.get('features' if alpha else 'payload') or '{}')
        if alpha:
            group = (r['version'], r['role'], r['stage'])
        else:
            group = (r['market'], saved.get('ledger_version', 'legacy'),
                     saved.get('signal_type', saved.get('kind', 'unspecified')))
        groups[group].append(r)
    result = []
    for group, rows in groups.items():
        endpoints = {}
        horizons = [(86400, '24h', '24'), (604800, '7d', '168')] if alpha else [
            (3600, '1h', '1h'), (21600, '6h', '6h'), (86400, '24h', '24h'), (604800, '7d', '7d')]
        for seconds, label, mark_key in horizons:
            due = valid = missing = waiting = excluded = 0
            returns = []
            for r in rows:
                opened = r['opened' if alpha else 'created']
                if now < opened + seconds:
                    continue
                due += 1
                mark = json.loads(r['horizons' if alpha else 'marks'] or '{}').get(mark_key, {})
                observed = mark.get('observed' if alpha else 'observed_at')
                value = mark.get('price_multiple' if alpha else 'return')
                if (finite(value) and finite(observed) and opened + seconds <= observed <= opened + seconds + 1800
                        and not r.get('suspect_jump')):
                    valid += 1
                    returns.append(value - 1 if alpha else value)
                elif now > opened + seconds + 1800:
                    missing += 1
                    excluded += finite(value)
                else:
                    waiting += 1
            endpoints[label] = {'due': due, 'valid': valid, 'missing': missing,
                                'in_grace': waiting, 'excluded': excluded,
                                'mean_return': statistics.mean(returns) if returns else None}
        peaks = [r['peak'] / r['entry'] for r in rows if finite(r['entry']) and r['entry'] > 0
                 and not r.get('suspect_jump')]
        drawdowns = [r['max_drawdown'] for r in rows if finite(r.get('max_drawdown'))]
        result.append({'group': list(group), 'tracks': len(rows),
                       'new_7d': sum(r['opened' if alpha else 'created'] >= now - 7 * DAY for r in rows),
                       'endpoints': endpoints, 'sampled_peak_multiple_max': max(peaks, default=None),
                       'sampled_drawdown_max': max(drawdowns, default=None), 'lead_time': None})
    return {'groups': result, 'unavailable_horizons': ['1h', '6h'] if alpha else []}


def database_report(path, name, now):
    with read_db(path) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        runtime = {r[0]: json.loads(r[1]) for r in db.execute('SELECT key,value FROM runtime')
                   if r[0] in ('health', 'latest', 'scan', 'fast', 'telegram', 'dingtalk')}
        health = runtime.get('scan' if name == 'alpha' else 'health', runtime.get('latest', {}))
        stamp = health.get('checked', health.get('as_of'))
        if isinstance(stamp, str):
            try:
                stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
            except ValueError:
                stamp = None
        channels = {}
        for table in ('outbox', 'dingtalk_outbox'):
            if table not in tables:
                continue
            for row in db.execute('SELECT state,attempts,created,sent,payload FROM ' + table + ' WHERE created>=?', (now - 7 * DAY,)):
                channel = ('dingtalk' if table == 'dingtalk_outbox' else 'telegram') if name == 'alpha' else json.loads(row['payload']).get('channel', 'legacy')
                stats = channels.setdefault(channel, {'states': {}, 'retried': 0, 'sent': 0, 'latency_max_seconds': None})
                stats['states'][row['state']] = stats['states'].get(row['state'], 0) + 1
                stats['retried'] += row['attempts'] > (1 if row['state'] == 'sent' else 0)
                if row['state'] == 'sent' and finite(row['sent']):
                    stats['sent'] += 1
                    delay = row['sent'] - row['created']
                    stats['latency_max_seconds'] = max(stats['latency_max_seconds'] or 0, delay)
        status = health.get('status', 'unknown')
        if name == 'stock':
            quality = health.get('data_quality', {})
            status = 'no_fresh_quotes' if not quality.get('fresh_rows') else 'partial' if health.get('errors') else 'ok'
        return {'status': status,
                'last_observation_age_seconds': max(0, now - stamp) if finite(stamp) else None,
                'version': health.get('version'), 'source_errors': health.get('errors', []),
                'delivery_7d_retained': channels,
                'outcomes': outcomes(db, 'evaluation_tracks' if name == 'alpha' else 'forward_tracks', now)}


def make_report(databases, x_state, now):
    report = {'as_of_utc': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'radars': {},
              'limits': ['No model API calls. Read-only outcomes; no orders or strategy changes.',
                         'Delivery counts cover retained queue records, not a full historical audit.',
                         'Missed-post rate and lead time are unknown without independent source truth.',
                         'Sampled returns and peaks exclude fees/slippage and are not realized profit.']}
    for name, path in databases.items():
        try:
            report['radars'][name] = database_report(path, name, now)
        except Exception as exc:
            report['radars'][name] = {'error': type(exc).__name__}
    if 'alpha' in databases and 'error' not in report['radars'].get('alpha', {}):
        for filename, key in [('fast-health.json', 'fast_lane'), ('evaluation-report.json', 'evaluation')]:
            path = databases['alpha'].parent / filename
            if path.exists():
                try:
                    value = json.loads(path.read_text())
                    report['radars']['alpha'][key] = {k: value[k] for k in (
                        'as_of', 'version', 'rule_version', 'status', 'errors', 'positions_by_mode') if k in value}
                except (OSError, ValueError):
                    report['radars']['alpha'][key] = {'error': 'unreadable'}
    try:
        value = json.loads(x_state.read_text())
        health = value.get('health', {})
        names = json.loads(X_ACCOUNTS.read_text())
        if not isinstance(names, list) or not names or any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z0-9_]+', n) for n in names):
            raise ValueError('x_account_configuration')
        accounts = {n.lower() for n in names}
        report['radars']['x'] = {
            'heartbeat_age_seconds': max(0, now - health.get('heartbeat', 0)),
            'configured_accounts_available': True,
            'accounts': {account: {'last_success_age_seconds': max(0, now - h.get('last_success', 0)),
                                   'consecutive_failures': h.get('failures', 0)}
                         for account, h in health.get('accounts', {}).items() if account in accounts},
            'missing_account_health': sorted(accounts - set(health.get('accounts', {}))),
            'pending_posts': len(value.get('pending', {})),
            'missed_posts': None, 'historical_delivery_failures': None,
        }
    except Exception as exc:
        report['radars']['x'] = {'error': type(exc).__name__}
    for service in SERVICES:
        result = subprocess.run(['systemctl', 'is-active', service], capture_output=True, text=True, timeout=5)
        report.setdefault('services', {})[service] = result.stdout.strip() or 'unknown'
    return report


def compact_report(report):
    summary = {k: report[k] for k in ('as_of_utc', 'services', 'disk', 'backup_errors') if k in report}
    summary['radars'] = {}
    for name, value in report['radars'].items():
        item = {k: value[k] for k in ('status', 'error', 'version', 'last_observation_age_seconds',
                'heartbeat_age_seconds', 'accounts', 'missing_account_health', 'pending_posts') if k in value}
        item['source_error_count'] = len(value.get('source_errors', []))
        item['source_error_examples'] = value.get('source_errors', [])[:4]
        if 'fast_lane' in value:
            item['fast_lane'] = value['fast_lane']
        item['delivery_7d_retained'] = value.get('delivery_7d_retained', {})
        endpoints = {}
        tracks = 0
        cohorts = []
        for group in value.get('outcomes', {}).get('groups', []):
            tracks += group['tracks']
            cohorts.append({'group': group['group'], 'tracks': group['tracks']})
            for label, counts in group['endpoints'].items():
                dest = endpoints.setdefault(label, {k: 0 for k in ('due', 'valid', 'missing', 'in_grace', 'excluded')})
                for k in dest:
                    dest[k] += counts[k]
        item['forward_tracks'] = tracks if name != 'x' else None
        item['cohorts'] = cohorts
        item['endpoint_counts'] = endpoints
        summary['radars'][name] = item
    summary['unknown'] = ['X missed-post rate and historical delivery failures', 'Lead time',
                          'Alpha 1h/6h endpoints', 'Executable profit and 100x success probability']
    summary['details'] = '/var/lib/radar-maintenance/latest-report.json'
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='write report/alerts and daily backups')
    args = parser.parse_args()
    now = time.time()
    disk = disk_status(shutil.disk_usage('/'))
    report = make_report(DATABASES, X_STATE, now)
    report['disk'] = disk
    report['raw_expired_bytes'] = prune_raw(DATABASES['alpha'].parent / 'raw', now, args.apply)
    if args.apply:
        HOME.mkdir(mode=0o700, exist_ok=True)
        os.chmod(HOME, 0o700)
        errors = []
        try:
            backups(HOME, DATABASES, X_STATE, now)
        except Exception as exc:
            errors.append(type(exc).__name__)
        report['backup_errors'] = errors
        atomic_json(HOME / 'latest-report.json', report)
        atomic_json(HOME / 'review-summary.json', compact_report(report))
        # A seven-day ring provides a compact weekly view without unbounded logs.
        day = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%d')
        atomic_json(HOME / f'report-daily-{day}.json', report)
        rotate(HOME, 'report-daily-', '.json', 8)
        week = datetime.fromtimestamp(now - datetime.fromtimestamp(now, timezone.utc).weekday() * DAY,
                                      timezone.utc).strftime('%Y%m%d')
        atomic_json(HOME / f'report-weekly-{week}.json', report)
        rotate(HOME, 'report-weekly-', '.json', 4)
        alert(DATABASES['alpha'], 'disk', disk['alert'],
              f"四套雷达磁盘{'告警' if disk['alert'] else '恢复'}：使用率 {disk['used_percent']}%，可用 {disk['free_gib']} GiB。", now)
        alert(DATABASES['alpha'], 'backup', bool(errors),
              '四套雷达备份失败，请检查维护报告。' if errors else '四套雷达自动备份已恢复。', now)
        incomplete = any('error' in r for r in report['radars'].values())
        alert(DATABASES['alpha'], 'report', incomplete,
              '四套雷达维护报告无法读取部分数据，请检查云端。' if incomplete else '四套雷达维护报告数据读取已恢复。', now)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    if any('error' in r for r in report['radars'].values()) or report.get('backup_errors'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
