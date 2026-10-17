"""Read-only cloud validation; never sends messages or uses existing login profiles."""
import json
import platform
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import x_monitor as m
from playwright.sync_api import sync_playwright

report = {
    'started_at': datetime.now(timezone.utc).isoformat(),
    'architecture': platform.machine(), 'kernel': platform.release(),
    'python': platform.python_version(), 'playwright': version('playwright'),
    'login_session': 'fresh browser; no X credentials supplied',
    'external_messages_sent': False,
    'network': {}, 'browser': {}, 'accounts': {},
}
def persist():
    Path('cloud-probe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)

for name, url in [('x', 'https://x.com/'), ('telegram', 'https://api.telegram.org/'), ('dingtalk', 'https://oapi.dingtalk.com/')]:
    start = time.monotonic()
    try:
        try:
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15) as response:
                status = response.status
        except HTTPError as exc:
            status = exc.code
        report['network'][name] = {'tls_http_response_received': True, 'status': status,
                                  'seconds': round(time.monotonic()-start, 2),
                                  'note': 'transport reachability only; not API authorization or delivery'}
    except Exception as exc:
        report['network'][name] = {'tls_http_response_received': False, 'error_type': type(exc).__name__}
persist()
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':1440,'height':1000})
            page.set_default_timeout(5000)
            page.set_content('<title>ARM smoke test</title><article data-testid="tweet"><a href="/demo/status/123"><time datetime="2026-09-26T00:00:00Z">now</time></a><div data-testid="tweetText">ARM 测试</div></article>')
            parsed = m.extract_tweet('demo', page.locator('article'))
            assert parsed and parsed.tweet_id == '123' and parsed.text == 'ARM 测试'
            report['browser'] = {'launched': True, 'version': browser.version, 'local_dom_extraction': True}
            persist()
            for account in ['binancezh', 'czbinanceprd', 'heyibinance']:
                start = time.monotonic()
                try:
                    tweets = m.scrape_account(page, account, max_items=20)
                    report['accounts'][account] = {'scrape_ok': True, 'count': len(tweets),
                        'tweet_ids': [t.tweet_id for t in tweets],
                        'published_at': [t.published_at for t in tweets],
                        'seconds': round(time.monotonic()-start, 2)}
                except Exception as exc:
                    try:
                        text = page.locator('body').inner_text(timeout=3000)[:800]
                    except Exception:
                        text = ''
                    report['accounts'][account] = {'scrape_ok': False, 'error_type': type(exc).__name__,
                        'error': str(exc)[:350], 'visible_page_excerpt': text,
                        'seconds': round(time.monotonic()-start, 2)}
                persist()
        finally:
            browser.close()
except Exception as exc:
    report['browser']['error_type'] = type(exc).__name__
    report['browser']['error'] = str(exc)[:800]
report['completed_at'] = datetime.now(timezone.utc).isoformat()
persist()
