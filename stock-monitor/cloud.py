#!/usr/bin/env python3
import json, signal, time
from config import Config
from radar import cycle
from storage import Store
from notify import send_channel

STOP = False
def stop(*_):
    global STOP; STOP = True

def deliver_one(cfg, store):
    return False  # 推送已迁移到 stock_scan/stock_track/stock_ta, 主服务只采集不投递
    now = time.time(); store.cleanup(now)
    enabled = [x for x, on in [('telegram', cfg.telegram_enabled), ('dingtalk', cfg.dingtalk_enabled)] if on]
    with store.db() as d: rows = d.execute("SELECT * FROM outbox WHERE state='pending' AND next_try<=? ORDER BY created LIMIT 20", (now,)).fetchall()
    row = next((r for r in rows if json.loads(r['payload']).get('channel') in enabled), None)
    if row is None: return False
    body = json.loads(row['payload'])
    try: send_channel(cfg, body['text'], body['channel'])
    except Exception as e:
        with store.db() as d: d.execute('UPDATE outbox SET attempts=attempts+1,next_try=?,error=? WHERE key=?', (now + min(900, 30 * 2 ** min(row['attempts'], 5)), type(e).__name__, row['key']))
    else:
        with store.db() as d: d.execute("UPDATE outbox SET state='sent',attempts=attempts+1,sent=?,error=NULL WHERE key=?", (now, row['key']))
    return True

def main():
    cfg = Config.load(); store = Store(cfg.data)
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    while not STOP:
        started = time.time()
        try:
            report = cycle(cfg, store)
            q = report.get('data_quality', {})
            summary = {'version': report.get('version'), 'as_of': report.get('as_of'),
                       'rows': q.get('rows'), 'fresh': q.get('fresh_rows'), 'stale': q.get('stale_rows'),
                       'source_errors': q.get('source_errors'), 'events': len(report.get('events', [])),
                       # 2026-10-25 补度量: 按市场拆开, 用来查「评估样本 83% 是美股、而池子 67% 是 A股」的成因。
                       # suppressed = 分数跳升够格但因 fresh=False 没发事件的行。只加字段, 不改行为。
                       'events_by_market': report.get('events_by_market'),
                       'suppressed': report.get('suppressed'),
                       'fresh_by_market': report.get('fresh_by_market'),
                       'errors': report.get('errors', [])}
            print(json.dumps(summary, ensure_ascii=False), flush=True)
        except Exception as e: print('stock_error:' + type(e).__name__, flush=True)
        for _ in range(8):
            if not deliver_one(cfg, store): break
        target = max(time.time() + 5, started + cfg.interval)
        while not STOP and time.time() < target: time.sleep(1)
if __name__ == '__main__': main()
