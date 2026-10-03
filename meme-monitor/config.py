from __future__ import annotations
import os,re
from dataclasses import dataclass
from pathlib import Path

def integer(env,name,default,low,high):
    try:v=int(env.get(name,str(default)))
    except ValueError:raise ValueError(f'{name} must be integer') from None
    if not low<=v<=high:raise ValueError(f'{name} must be {low}..{high}')
    return v

def secret(env,name):
    p=env.get(name+'_FILE')
    value=Path(p).read_text().strip() if p else env.get(name,'').strip()
    # Permit a root-owned EnvironmentFile containing NAME=value while keeping
    # the existing raw-secret-file convention compatible.
    if p and ('\n' in value or value.startswith(name+'=')):
        for line in value.splitlines():
            if line.startswith(name+'='):
                return line.split('=',1)[1].strip().strip('"').strip("'")
    return value

@dataclass(frozen=True)
class Config:
    data: Path; interval:int; chains:tuple[str,...]; max_candidates:int; timeout:int
    wallet_file:Path; kol_file:Path; x_feed_file:Path
    telegram_enabled:bool; telegram_token:str; telegram_chat:str
    dingtalk_enabled:bool; dingtalk_webhook:str; dingtalk_secret:str; dingtalk_keyword:str
    bsc_rpc:str; solana_rpc:str; extra_sources:Path; public_feed_db:Path|None
    safety_enabled:bool; safety_max_checks:int; safety_cache_seconds:int
    gmgn_api_key:str; gmgn_timeout:int; gmgn_max_checks:int
    binance_web3_file:Path|None; binance_web3_max_age:int; binance_web3_live:bool; binance_web3_live_checks:int
    @classmethod
    def load(cls,env=None):
        e=os.environ if env is None else env
        chains=tuple(x.strip().lower() for x in e.get('MEME_CHAINS','bsc,solana,robinhood,xlayer,arc,stable').split(',') if x.strip())
        if not chains or any(x not in ('bsc','solana','robinhood','xlayer','arc','stable') for x in chains):raise ValueError('unsupported MEME_CHAINS')
        te=e.get('MEME_TELEGRAM_ENABLED','false').lower()=='true'; token=secret(e,'MEME_TELEGRAM_BOT_TOKEN');chat=secret(e,'MEME_TELEGRAM_CHAT_ID')
        if te and (not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token) or not re.fullmatch(r'-?[0-9]+',chat)):raise ValueError('configure meme Telegram credentials')
        de=e.get('MEME_DINGTALK_ENABLED','false').lower()=='true'; webhook=secret(e,'MEME_DINGTALK_WEBHOOK')
        if de and ('oapi.dingtalk.com/robot/send' not in webhook):raise ValueError('configure meme DingTalk webhook')
        return cls(Path(e.get('MEME_DATA','/var/lib/meme-radar')),integer(e,'MEME_INTERVAL_SECONDS',60,20,3600),chains,integer(e,'MEME_MAX_CANDIDATES',40,5,200),integer(e,'MEME_HTTP_TIMEOUT_SECONDS',10,3,30),Path(e.get('MEME_SMART_WALLETS_FILE','/var/lib/meme-radar/smart_wallets.json')),Path(e.get('MEME_KOL_EVENTS_FILE','/var/lib/meme-radar/kol_events.jsonl')),Path(e.get('MEME_X_FEED_FILE','/var/lib/meme-radar/x_mentions.jsonl')),te,token,chat,de,webhook,secret(e,'MEME_DINGTALK_SECRET'),e.get('MEME_DINGTALK_KEYWORD','DT'),e.get('BSC_RPC_URL','https://bsc-dataseed.binance.org'),e.get('SOLANA_RPC_URL','https://api.mainnet-beta.solana.com'),Path(e.get('MEME_EXTRA_SOURCES_FILE','/var/lib/meme-radar/extra_sources.json')),Path(e['MEME_X_PUBLIC_FEED_DB']) if e.get('MEME_X_PUBLIC_FEED_DB') else None,e.get('MEME_SAFETY_ENABLED','true').lower()=='true',integer(e,'MEME_SAFETY_MAX_CHECKS',12,1,40),integer(e,'MEME_SAFETY_CACHE_SECONDS',900,60,86400),secret(e,'GMGN_API_KEY'),integer(e,'GMGN_HTTP_TIMEOUT_SECONDS',10,3,30),integer(e,'GMGN_MAX_CHECKS',8,0,40),Path(e['MEME_BINANCE_WEB3_EVIDENCE_FILE']) if e.get('MEME_BINANCE_WEB3_EVIDENCE_FILE') else None,integer(e,'MEME_BINANCE_WEB3_MAX_AGE',1800,60,86400),e.get('MEME_BINANCE_WEB3_LIVE_ENABLED','false').lower()=='true',integer(e,'MEME_BINANCE_WEB3_LIVE_CHECKS',4,0,12))
