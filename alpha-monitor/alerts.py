"""Acknowledged critical alerts. Receipt by an API is not a user's acknowledgment."""
import json,time

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS important_alerts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,base TEXT NOT NULL,position_id INTEGER,text TEXT NOT NULL,
      created REAL NOT NULL,last_emit REAL NOT NULL,repeats INTEGER NOT NULL DEFAULT 0,
      state TEXT NOT NULL DEFAULT 'open',acknowledged REAL);
    CREATE UNIQUE INDEX IF NOT EXISTS one_open_important ON important_alerts(base) WHERE state='open';
    ''')

def queue(db,key,text,now):
    db.execute('INSERT OR IGNORE INTO outbox(key,created,payload) VALUES(?,?,?)',(key,now,json.dumps({'kind':'ops','text':text},ensure_ascii=False)))

def raise_alert(db,base,text,now,position_id=None):
    row=db.execute("SELECT id FROM important_alerts WHERE base=? AND state='open'",(base,)).fetchone()
    if row:return row[0]
    cur=db.execute('INSERT INTO important_alerts(base,position_id,text,created,last_emit) VALUES(?,?,?,?,?)',(base,position_id,text,now,now));aid=cur.lastrowid
    queue(db,f'important:{aid}:0',text+f'\n重要提醒 #{aid}：Telegram 发送 /ack {aid} 确认已读；不执行交易。',now)
    return aid

def resolve(db,base,now):
    db.execute("UPDATE important_alerts SET state='resolved' WHERE base=? AND state='open'",(base,))
    cancel_pending(db)

def cancel_pending(db):
    # Stop unsent repeats after /ack, /close or recovery. Already accepted messages cannot be recalled.
    # Work from the small pending queue, not every historical alert x every outbox row.
    for table in ('outbox','dingtalk_outbox'):
        if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
            db.execute(f"""UPDATE {table} SET state='skipped',error='acknowledged_or_resolved'
                WHERE state='pending' AND key LIKE 'important:%'
                AND EXISTS (SELECT 1 FROM important_alerts a
                    WHERE a.id=CAST(substr({table}.key,11,instr(substr({table}.key,11),':')-1) AS INTEGER)
                    AND {table}.key LIKE 'important:' || a.id || ':%' AND a.state<>'open')""")

def reminders(store,now=None):
    now=time.time() if now is None else now
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE important_alerts SET state='expired' WHERE state='open' AND created<?",(now-86400,))
        cancel_pending(db)
        for a in db.execute("SELECT * FROM important_alerts WHERE state='open' AND repeats<3 AND last_emit<=?",(now-300,)).fetchall():
            count=a['repeats']+1
            text=(f'🔁 未确认重要提醒 #{a["id"]}（补提醒 {count}/3）\n以下是首次触发记录，价格不是当前报价：\n'+a['text']+f'\nTelegram /ack {a["id"]} 确认；补提醒最多3次，/pending 可查看待确认项。')
            queue(db,f'important:{a["id"]}:{count}',text,now)
            db.execute('UPDATE important_alerts SET repeats=?,last_emit=? WHERE id=?',(count,now,a['id']))
