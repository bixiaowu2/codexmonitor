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
    return Path(p).read_text().strip() if p else env.get(name,'').strip()

@dataclass(frozen=True)
class Config:
    data: Path; interval:int; chains:tuple[str,...]; max_candidates:int; timeout:int
    wallet_file:Path; kol_file:Path; x_feed_file:Path
    telegram_enabled:bool; telegram_token:str; telegram_chat:str
    dingtalk_enabled:bool; dingtalk_webhook:str; dingtalk_secret:str; dingtalk_keyword:str
    bsc_rpc:str; solana_rpc:str; extra_sources:Path; public_feed_db:Path|None
    @classmethod
    def load(cls,env=None):
        e=os.environ if env is None else env
        chains=tuple(x.strip().lower() for x in e.get('MEME_CHAINS','bsc,solana,robinhood,xlayer,arc,stable').split(',') if x.strip())
        if not chains or any(x not in ('bsc','solana','robinhood','xlayer','arc','stable') for x in chains):raise ValueError('unsupported MEME_CHAINS')
        te=e.get('MEME_TELEGRAM_ENABLED','false').lower()=='true'; token=secret(e,'MEME_TELEGRAM_BOT_TOKEN');chat=secret(e,'MEME_TELEGRAM_CHAT_ID')
        if te and (not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token) or not re.fullmatch(r'-?[0-9]+',chat)):raise ValueError('configure meme Telegram credentials')
        de=e.get('MEME_DINGTALK_ENABLED','false').lower()=='true'; webhook=secret(e,'MEME_DINGTALK_WEBHOOK')
        if de and ('oapi.dingtalk.com/robot/send' not in webhook):raise ValueError('configure meme DingTalk webhook')
        return cls(Path(e.get('MEME_DATA','/var/lib/meme-radar')),integer(e,'MEME_INTERVAL_SECONDS',60,20,3600),chains,integer(e,'MEME_MAX_CANDIDATES',40,5,200),integer(e,'MEME_HTTP_TIMEOUT_SECONDS',10,3,30),Path(e.get('MEME_SMART_WALLETS_FILE','/var/lib/meme-radar/smart_wallets.json')),Path(e.get('MEME_KOL_EVENTS_FILE','/var/lib/meme-radar/kol_events.jsonl')),Path(e.get('MEME_X_FEED_FILE','/var/lib/meme-radar/x_mentions.jsonl')),te,token,chat,de,webhook,secret(e,'MEME_DINGTALK_SECRET'),e.get('MEME_DINGTALK_KEYWORD','DT'),e.get('BSC_RPC_URL','https://bsc-dataseed.binance.org'),e.get('SOLANA_RPC_URL','https://api.mainnet-beta.solana.com'),Path(e.get('MEME_EXTRA_SOURCES_FILE','/var/lib/meme-radar/extra_sources.json')),Path(e['MEME_X_PUBLIC_FEED_DB']) if e.get('MEME_X_PUBLIC_FEED_DB') else None)
