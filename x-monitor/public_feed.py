"""Public post bridge. No credentials, cookies or notification routes enter this DB."""
import contextlib,json,os,re,sqlite3,time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

CHAINS={'bsc','solana','robinhood','xlayer','arc','stable'}
CATEGORIES={'official','research','risk_research','ecosystem','market_commentator'}

def load_accounts(path):
    raw=json.loads(Path(path).read_text())
    if not isinstance(raw,dict) or not isinstance(raw.get('accounts'),list):raise ValueError('invalid_accounts_registry')
    out={}
    for item in raw['accounts']:
        if not isinstance(item,dict):raise ValueError('invalid_account_entry')
        if not item.get('enabled',True):continue
        handle=str(item.get('handle','')).lstrip('@').lower()
        if not re.fullmatch(r'[a-z0-9_]{1,15}',handle) or handle in out:raise ValueError('invalid_or_duplicate_handle')
        if not set(item.get('chains',[]))<=CHAINS or not item.get('chains'):raise ValueError('invalid_chains')
        if item.get('category') not in CATEGORIES:raise ValueError('invalid_category')
        if not item.get('evidence') or not all(str(u).startswith('https://') for u in item['evidence']):raise ValueError('missing_public_evidence')
        interval=int(item.get('interval_seconds',600))
        if not 120<=interval<=86400:raise ValueError('invalid_interval')
        out[handle]=dict(item,handle=handle,interval_seconds=interval)
    if len(out)>60:raise ValueError('too_many_accounts')
    return out

class PublicFeed:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as d:
            d.executescript('''CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,payload TEXT);
                CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,account TEXT,watched_account TEXT,url TEXT,text TEXT,published REAL,observed REAL);
                CREATE INDEX IF NOT EXISTS recent_posts ON posts(published);
                CREATE TABLE IF NOT EXISTS accounts(account TEXT PRIMARY KEY,last_attempt REAL,last_success REAL,next_due REAL,failures INTEGER,error TEXT);''')
        self.path.chmod(0o640)
    @contextlib.contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=3)
        try:
            with conn:yield conn
        finally:conn.close()
    def set_registry(self, registry):
        with self.db() as d:d.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('accounts',json.dumps(registry,ensure_ascii=False)))
    def success(self,account,tweets,interval=600,now=None):
        now=time.time() if now is None else now
        count=0
        with self.db() as d:
            for tweet in tweets:
                url=urlsplit(tweet.url)
                m=re.fullmatch(r'/([A-Za-z0-9_]+)/status/(\d+)',url.path)
                # A repost card is not evidence that the watched account authored it.
                if url.scheme!='https' or url.netloc not in ('x.com','twitter.com') or not m or m[1].lower()!=account.lower() or str(tweet.tweet_id)!=m[2]:continue
                try:stamp=datetime.fromisoformat(tweet.published_at.replace('Z','+00:00')).timestamp()
                except (ValueError,TypeError):continue
                if not 0<=now-stamp<=86400:continue
                cur=d.execute('INSERT OR IGNORE INTO posts VALUES(?,?,?,?,?,?,?)',(m[2],account.lower(),account.lower(),tweet.url,tweet.text[:12000],stamp,now)); count += max(0, cur.rowcount)
            d.execute('INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,?,?)',(account,now,now,now+interval,0,None))
            d.execute('DELETE FROM posts WHERE published<?',(now-86400,))
            d.execute('DELETE FROM posts WHERE id IN (SELECT id FROM posts ORDER BY published DESC LIMIT -1 OFFSET 20000)')
        return count
    def failure(self,account,code,interval=600,now=None):
        now=time.time() if now is None else now
        with self.db() as d:
            row=d.execute('SELECT last_success,failures FROM accounts WHERE account=?',(account,)).fetchone()
            last,n=row if row else (0,0); n+=1
            d.execute('INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,?,?)',(account,now,last,now+min(3600,interval*2**min(n-1,3)),n,code))
    def due(self,registry,exclude=(),limit=3,now=None):
        now=time.time() if now is None else now
        with self.db() as d:states={r[0]:r[1] for r in d.execute('SELECT account,next_due FROM accounts')}
        items=[a for a in registry if a not in exclude and states.get(a,0)<=now]
        # Fairness: an unpolled account cannot be starved by a frequent account.
        return sorted(items,key=lambda a:(states.get(a,0),registry[a].get('priority',10),a))[:limit]
