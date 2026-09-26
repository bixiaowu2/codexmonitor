from __future__ import annotations
import json, os, re
from dataclasses import dataclass
from pathlib import Path

def integer(env, name, default, low, high):
    try: value = int(env.get(name, str(default)))
    except ValueError: raise ValueError(f'{name} must be integer') from None
    if not low <= value <= high: raise ValueError(f'{name} must be {low}..{high}')
    return value

def secret(env, name):
    p = env.get(name + '_FILE')
    return Path(p).read_text().strip() if p else env.get(name, '').strip()

@dataclass(frozen=True)
class Config:
    data: Path; universe: Path; interval: int; ranking_interval: int; timeout: int
    max_candidates: int; telegram_enabled: bool; telegram_token: str; telegram_chat: str
    dingtalk_enabled: bool; dingtalk_webhook: str; dingtalk_secret: str; dingtalk_keyword: str

    @classmethod
    def load(cls, env=None):
        e = os.environ if env is None else env
        te = e.get('STOCK_TELEGRAM_ENABLED', 'false').lower() == 'true'
        token, chat = secret(e, 'STOCK_TELEGRAM_BOT_TOKEN'), secret(e, 'STOCK_TELEGRAM_CHAT_ID')
        if te and (not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token) or not re.fullmatch(r'-?[0-9]+', chat)):
            raise ValueError('configure stock Telegram credentials')
        de = e.get('STOCK_DINGTALK_ENABLED', 'false').lower() == 'true'
        webhook = secret(e, 'STOCK_DINGTALK_WEBHOOK')
        if de and 'oapi.dingtalk.com/robot/send' not in webhook: raise ValueError('configure stock DingTalk webhook')
        return cls(Path(e.get('STOCK_DATA', '/var/lib/stock-radar')), Path(e.get('STOCK_UNIVERSE', '/var/lib/stock-radar/universe.json')),
                   integer(e, 'STOCK_INTERVAL_SECONDS', 300, 60, 3600), integer(e, 'STOCK_RANKING_INTERVAL_SECONDS', 3600, 900, 86400),
                   integer(e, 'STOCK_HTTP_TIMEOUT_SECONDS', 12, 3, 30), integer(e, 'STOCK_MAX_CANDIDATES', 30, 5, 100), te, token, chat,
                   de, webhook, secret(e, 'STOCK_DINGTALK_SECRET'), e.get('STOCK_DINGTALK_KEYWORD', '股票雷达'))
