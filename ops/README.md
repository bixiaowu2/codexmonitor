# Radar maintenance and low-cost review

The server runs `radar_maintenance.py --apply` hourly using a systemd timer.
It uses Python's standard library only. It reads local state, generates compact
reports, checks storage, and takes online backups. It makes no model API calls,
changes no trading rules, and submits no orders.

## Retention and notifications

- Alert via the existing Alpha Telegram and DingTalk queues at 80% disk usage
  or less than 10 GiB free. One alert per incident, at most one reminder/day,
  and one recovery notice. Channel delivery remains independent.
- Keep original signal/events, forward/control ledgers, rule versions,
  positions, pending messages and browser login state. Radar cleanup rolls off
  raw snapshots and completed notification records, not research signals.
- Remove only old SHA256-named JSON cache files under Alpha's `raw/` after
  30 days. Symlinks and other filenames are excluded.
- SQLite online backups: seven daily and four weekly per radar; include X's
  atomically saved deduplication/pending state. Files are private (0600) in
  `/var/lib/radar-maintenance/backups` (0700). Existing repair backups are
  untouched. Backup is skipped if it risks leaving less than 10 GiB free.
- Rotate Alpha scan/fast file logs daily, also when exceeding 20 MiB at the
  scheduled logrotate check; seven archives, age limit 30 days. `copytruncate`
  avoids restarting a writer but can lose a small amount of log data during
  rotation. Trading evidence stays in the databases.
- Shared system journals and other applications are untouched. Their disk
  usage still contributes to the disk alert. This is not a limit on all logs
  or browser-profile disk usage.
- All copies currently reside on the same server. They do not protect against
  instance/disk loss; an off-server backup destination is still needed.

## Review evidence and cadence

Read `/var/lib/radar-maintenance/review-summary.json` first for compact health,
cohort and endpoint counts. Only consult the detailed report for abnormalities.
`/var/lib/radar-maintenance/latest-report.json` contains all four service states,
latest data ages, source errors, retained seven-day delivery results and forward
outcomes. Keep eight daily and four weekly reports. Weekly reports are refreshed
throughout their UTC week. Read the latest report and daily ring first; only
read logs/code for an identified abnormality.

Meme/stock outcomes are grouped by chain/listing market, ledger version and
recorded signal type, at 1h/6h/24h/7d. Alpha is grouped by rule version,
signal/control role and stage; its existing ledger has 24h/7d endpoints but
no 1h/6h observations. Missing and late endpoints remain excluded. Peaks and
drawdowns are sampled price paths, not executable or realized returns.

X health reports core accounts from `/etc/radar-maintenance-x-accounts.json`,
excluding historical inactive entries. Update this public list when the core
X monitor account list changes; no `.env` or credentials are loaded.
Extra-account feed health is not included in this compact check.
X has no independent timeline ground truth or long-term delivery audit;
missed-post count/rate is explicitly unknown. Queue statistics cover retained
records, not every historical attempt. Lead time is also unknown. These reports
must not be treated as proof of no missed messages or a profitable strategy.

Weekly Codex review: inspect report, data coverage, failed deliveries and deployed
versions; investigate/fix evidenced faults only. Monthly review: use accumulated
forward cohorts to evaluate sources and candidate rules, with chronological
holdout and asset grouping. Insufficient samples mean retain strategy. No code
change means health checks only; changes need targeted tests, backup, verified
deployment and GitHub sync. Model selection depends on the user's API provider;
this script does not switch or call models.

## Install and rollback

Install the script in `/opt/radar-maintenance/` (root-owned, 0644), the unit/timer
in `/etc/systemd/system/`, and `radar-logrotate` as
`/etc/radar-maintenance.logrotate`. Install `logrotate` if absent; disable its
newly installed global timer and use this unit's private configuration/state
to avoid changing unrelated log retention. Install `x-core-accounts.json` as
`/etc/radar-maintenance-x-accounts.json`. Validate with `systemd-analyze verify`
and `logrotate -d`, run without `--apply` first, then enable the timer and run
one apply cycle. A healthy cycle sends no routine notification.

Rollback: disable only `radar-maintenance.timer`, remove its new logrotate
configuration `/etc/radar-maintenance.logrotate`, and restore pre-maintenance
radar code if required. Do not delete
backups or restore a database over live writers; that requires a separate,
controlled recovery. Leave other services/timers intact.

Local tests: `python3 -m unittest discover -s ops -v` plus each changed radar's
retention/notification tests. No secrets, runtime reports or databases belong
in Git.

## Verified deployment (2026-10-01 Beijing)

Maintenance tests (9), Alpha cloud tests (13), Meme retention/core/forward tests
(37), and stock retention/core/forward tests (25) passed. Cloud script and three
retention modules match local SHA256; service completed successfully, hourly
timer enabled, and all four radar services remained active after deployment.
The first six SQLite daily/weekly backups passed `quick_check` with private
0600 permissions. Report generation reads existing ledgers without modifying
outcomes; X pending state is backed up. Database and code backups precede the
retention deployment. Disk after first backup was 69.9% used with 28.84 GiB free.

Live reports still show partial Alpha/Meme coverage, source 429s and missing
forward endpoints. This maintenance release exposes those gaps; it does not
repair external data availability or justify new strategy thresholds.

## Actual holdings and daily context

`radar-holdings.service` is a supporting oneshot worker for the existing radars,
not a fifth radar. Install the six support modules alongside the maintenance
script and the unit/timer in `/etc/systemd/system/`. It reads the existing
private environment files; credentials do not belong in this directory or Git.
The unit limits memory to 192 MiB and CPU to 25%. Only Meme/stock Telegram
updates are polled here; Alpha retains its existing command consumer.

Actual positions, atomic command offsets and independent delivery queues live
in `/var/lib/radar-holdings/holdings.sqlite` (directory 0700, database 0600).
Reference Alpha positions are excluded from actual holding analysis. See
[Telegram registration](Telegram持仓登记.md) for currency, command formats and
the meaning of cost updates. The worker cannot place orders.

Daily context is owned by this helper: Alpha/Meme 09:00 Beijing, separate A/HK
messages at 10:00 Beijing weekdays, US 10:00 New York weekdays with DST. The
30-minute queue window prevents late stale morning catchups. Empty/closed-market
coverage stays explicit. Each message includes the relevant registration format.
Do not also enable an older daily-summary implementation in a radar: it would
create competing daily messages.

Maintenance includes helper health and backs up its database. Sent-message
symptom counts indicate missing data or ambiguous wording, not proven trading
losses; X still lacks a complete historical sent-message ledger.

Rollback only the helper: stop/disable `radar-holdings.timer`, wait for the
oneshot to finish, and restore support code from the release backup if needed.
Keep its journal and the four original services. Never restore a live SQLite
database over writers. Release details are in [the release record](发布记录-20261001.md).
