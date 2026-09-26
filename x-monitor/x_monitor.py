#!/usr/bin/env python3
"""Monitor X accounts without using the paid X API.

The first run opens a persistent Chromium profile so the operator can log in to X
manually. Subsequent runs reuse that session and poll configured profiles every
POLL_SECONDS seconds.
"""
from __future__ import annotations

import json
import argparse
import base64
import hashlib
import hmac
import logging
import os
import re
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse, parse_qsl, urlunparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
from public_feed import PublicFeed, load_accounts


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("x-monitor")
STOP = False


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def post_json(url: str, payload: dict[str, Any], timeout: int = 15, telegram: bool = False) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            result = response.read().decode("utf-8", errors="replace")
            if response.status >= 300:
                raise RuntimeError(f"webhook HTTP {response.status}")
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError as exc:
                raise RuntimeError("webhook returned invalid JSON") from exc
            if telegram:
                if not isinstance(parsed, dict) or parsed.get("ok") is not True:
                    raise RuntimeError("Telegram rejected message or returned invalid response")
                return
            if not isinstance(parsed, dict) or type(parsed.get("errcode")) is not int:
                raise RuntimeError("webhook response missing integer errcode")
            if parsed["errcode"] != 0:
                raise RuntimeError(f"webhook rejected message, errcode={parsed['errcode']}")
    except HTTPError as exc:
        raise RuntimeError(f"webhook HTTP {exc.code}") from None
    except (URLError, OSError):
        raise RuntimeError("webhook network error") from None


def signed_dingtalk_url(url: str, secret: str | None = None) -> str:
    """Add DingTalk's optional timestamp/signature parameters when configured."""
    secret = (os.getenv("DINGTALK_SECRET", "") if secret is None else secret).strip()
    if not secret:
        return url
    timestamp = str(int(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    sign = base64.b64encode(hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()).decode()
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.update({"timestamp": timestamp, "sign": sign})
    return urlunparse(parsed._replace(query=urlencode(params)))


@dataclass(frozen=True)
class Tweet:
    account: str
    tweet_id: str
    url: str
    text: str
    published_at: str
    kind: str = "新帖"

    @property
    def title(self) -> str:
        return f"X @{self.account} {self.kind}"


@dataclass(frozen=True)
class WebhookTarget:
    kind: str
    url: str
    secret: str = ""
    chat_id: str = ""
    keyword: str = ""


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def truncate_utf8(text: str, max_bytes: int) -> str:
    """Truncate by UTF-8 bytes so webhook payload limits are respected."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    clipped = encoded[: max_bytes - len("…".encode("utf-8"))]
    return clipped.decode("utf-8", errors="ignore") + "…"


# X currently serves both its older tweet cards and a newer timeline layout.
CARD_SELECTOR = 'article[data-testid="tweet"], [data-timeline-entry][data-href*="/status/"]:not([data-timeline-entry] [data-timeline-entry])'


def extract_tweet(account: str, article: Any) -> Tweet | None:
    try:
        canonical = article.get_attribute("data-href")
        time_locator = article.locator("time").first
        published_at = ""
        if not canonical and time_locator.count():
            canonical = time_locator.locator("xpath=ancestor::a[1]").get_attribute("href")
            published_at = time_locator.get_attribute("datetime") or ""
        if not canonical:
            canonical = article.locator('a[href*="/status/"]').first.get_attribute("href")
        match = re.search(r"/([A-Za-z0-9_]+)/status/(\d+)", canonical or "")
        if not match:
            return None
        author, tweet_id = match.groups()
        url = f"https://x.com/{author}/status/{tweet_id}"
        if not published_at:
            # Public new-layout cards display relative dates without <time>.
            # X snowflake IDs encode the creation timestamp in milliseconds.
            stamp = ((int(tweet_id) >> 22) + 1288834974657) / 1000
            published_at = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        text_locator = article.locator('[data-testid="tweetText"]').first
        if not text_locator.count():
            text_locator = article.locator('div[dir="auto"].whitespace-pre-wrap').first
        text = normalize_text(text_locator.inner_text(timeout=1500)) if text_locator.count() else ""
        return Tweet(account, tweet_id, url, text, published_at)
    except Exception as exc:
        LOG.debug("could not parse tweet card for @%s: %s", account, type(exc).__name__)
        return None


def scrape_account(page: Page, account: str, max_items: int = 20, navigation_ms: int = 45000, cards_ms: int = 15000, scrolls: int = 5) -> list[Tweet]:
    url = f"https://x.com/{quote(account)}"
    LOG.info("checking @%s", account)
    response = page.goto(url, wait_until="domcontentloaded", timeout=navigation_ms)
    if response and response.status >= 400:
        raise RuntimeError(f"X returned HTTP {response.status}; check network, X access restrictions and login state")
    try:
        page.locator(CARD_SELECTOR).first.wait_for(timeout=cards_ms)
    except PlaywrightTimeoutError:
        raise RuntimeError(f"no tweet cards found for @{account}; login, rate limit, or selector change may be involved")
    found: dict[str, Tweet] = {}
    # Read each viewport before scrolling: X virtualizes timeline cards.
    for _ in range(scrolls):
        cards = page.locator(CARD_SELECTOR)
        before = len(found)
        for index in range(cards.count()):
            tweet = extract_tweet(account, cards.nth(index))
            if tweet:
                found[tweet.tweet_id] = tweet
            if len(found) >= max_items:
                break
        if len(found) >= max_items:
            break
        if len(found) == before and found:
            break
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(1000)
    if not found:
        raise RuntimeError(f"no parseable tweets for @{account}; baseline unchanged")
    return list(found.values())


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen": {}, "initialized_accounts": [], "pending": {}, "watermarks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("seen"), dict):
            raise ValueError("invalid state structure")
        for seen in data["seen"].values():
            if not isinstance(seen, dict):
                raise ValueError("invalid seen state")
        # Migrate only accounts with an existing successful baseline.
        data.setdefault("initialized_accounts", [a for a, seen in data["seen"].items()
                                                 if data.get("initialized") and seen])
        data.setdefault("pending", {})
        data.setdefault("watermarks", {})
        if (not isinstance(data["initialized_accounts"], list)
                or not isinstance(data["pending"], dict)
                or not isinstance(data["watermarks"], dict)):
            raise ValueError("invalid state structure")
        for a in data["initialized_accounts"]:
            data["watermarks"].setdefault(a, max([int(i) for i in data["seen"].get(a, {})] or [0]))
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("cannot load state; restore a valid backup instead of resetting silently") from exc


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def format_markdown(tweet: Tweet) -> str:
    text = tweet.text or "（无文字内容，可能是图片或视频帖）"
    text = truncate_utf8(text, 3000)
    published = tweet.published_at.replace("T", " ").replace("Z", " UTC") if tweet.published_at else ""
    return f"**{tweet.title}**\n\n{text}\n\n[打开原帖]({tweet.url})\n\n{published}" 


def _parse_targets(values: list[Any], where: str) -> list[WebhookTarget]:
    targets: list[WebhookTarget] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise RuntimeError(f"{where}[{index}] must be an object")
        kind = str(value.get("type", "")).strip().lower()
        url = str(value.get("url", "")).strip()
        secret = str(value.get("secret", "")).strip()
        chat_id = str(value.get("chat_id", "")).strip()
        if kind not in {"wecom", "dingtalk", "telegram"} or not url:
            raise RuntimeError(f"{where}[{index}] requires type=wecom|dingtalk|telegram and url")
        if kind == "telegram" and not chat_id:
            raise RuntimeError(f"{where}[{index}] requires Telegram chat_id")
        keyword = str(value.get("keyword", os.getenv("DINGTALK_KEYWORD", "DT") if kind == "dingtalk" else "")).strip()
        targets.append(WebhookTarget(kind, url, secret, chat_id, keyword))
    return targets


def load_routes(path: Path) -> dict[str, list[WebhookTarget]]:
    """Load optional per-account webhook routing from a JSON file."""
    if not path.exists():
        if os.getenv("ROUTES_FILE"):
            raise RuntimeError("configured ROUTES_FILE does not exist")
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read ROUTES_FILE {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("ROUTES_FILE must contain a JSON object")
    routes: dict[str, list[WebhookTarget]] = {}
    accounts = raw.get("accounts", {})
    if not isinstance(accounts, dict):
        raise RuntimeError("ROUTES_FILE.accounts must be an object")
    for account, values in accounts.items():
        if not isinstance(values, list):
            raise RuntimeError(f"accounts.{account} must be a list")
        routes[account.lower().lstrip("@")] = _parse_targets(values, f"accounts.{account}")
    if "default" in raw and not isinstance(raw["default"], list):
        raise RuntimeError("default must be a list")
    if isinstance(raw.get("default"), list):
        routes["*"] = _parse_targets(raw["default"], "default")
    return routes


def global_targets() -> list[WebhookTarget]:
    targets: list[WebhookTarget] = []
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if bool(token) != bool(chat_id):
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be configured")
    if token:
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise RuntimeError("invalid TELEGRAM_BOT_TOKEN format")
        targets.append(WebhookTarget("telegram", f"https://api.telegram.org/bot{token}/sendMessage", chat_id=chat_id))
    if (url := os.getenv("DINGTALK_WEBHOOK", "").strip()):
        targets.append(WebhookTarget("dingtalk", url, os.getenv("DINGTALK_SECRET", "").strip(), keyword=os.getenv("DINGTALK_KEYWORD", "DT").strip()))
    if (url := os.getenv("WECOM_WEBHOOK", "").strip()):
        targets.append(WebhookTarget("wecom", url))
    return targets


def targets_for(account: str, routes: dict[str, list[WebhookTarget]]) -> list[WebhookTarget]:
    return list(dict.fromkeys(routes.get(account.lower(), routes.get("*", global_targets()))))


def target_key(target: WebhookTarget) -> str:
    # Store an opaque identifier, never the webhook secret, in the outbox.
    identity = f"{target.kind}:{target.url}"
    if target.kind == "telegram":
        identity += f":{target.chat_id}"
    return hashlib.sha256(identity.encode()).hexdigest()


def send_target(tweet: Tweet, target: WebhookTarget) -> None:
    markdown = format_markdown(tweet)
    if target.kind == "telegram":
        # Plain text avoids parse failures for arbitrary Markdown in X posts.
        text = f"{tweet.title}\n\n{truncate_utf8(tweet.text, 3000)}\n\n{tweet.url}\n{tweet.published_at}"
        post_json(target.url, {"chat_id": target.chat_id, "text": text,
                              "link_preview_options": {"is_disabled": True}}, timeout=10, telegram=True)
        return
    if target.kind == "dingtalk":
        url = signed_dingtalk_url(target.url, target.secret)
        keyword = target.keyword or os.getenv("DINGTALK_KEYWORD", "DT").strip()
        title = f"{keyword} | {tweet.title}" if keyword else tweet.title
        content = f"{keyword}\n\n{markdown}" if keyword else markdown
        payload = {"msgtype": "markdown", "markdown": {"title": title, "text": content}}
    else:
        url = target.url
        payload = {"msgtype": "markdown", "markdown": {"content": markdown}}
    post_json(url, payload, timeout=10)


def notify(tweet: Tweet, routes: dict[str, list[WebhookTarget]] | None = None) -> None:
    targets = targets_for(tweet.account, routes or {})
    if not targets:
        raise RuntimeError("no webhook configured; no message sent")
    errors = []
    for target in targets:
        try:
            send_target(tweet, target)
        except Exception:
            errors.append(target.kind)
    if errors:
        raise RuntimeError("notification failed for: " + ", ".join(errors))


def ingest(state: dict[str, Any], account: str, tweets: list[Tweet],
           routes: dict[str, list[WebhookTarget]], path: Path) -> None:
    if not tweets:
        raise RuntimeError("empty scrape cannot initialize an account")
    targets = targets_for(account, routes)
    if not targets:
        raise RuntimeError(f"no delivery targets for @{account}")
    seen = state["seen"].setdefault(account, {})
    baseline = account not in state["initialized_accounts"]
    watermark = state["watermarks"].get(account, 0)
    for tweet in sorted(tweets, key=lambda t: int(t.tweet_id)):
        if tweet.tweet_id in seen:
            continue
        if not baseline and int(tweet.tweet_id) > watermark:
            key = f"{account}:{tweet.tweet_id}"
            state["pending"].setdefault(key, {
                "tweet": asdict(tweet), "remaining": list(dict.fromkeys(target_key(t) for t in targets)),
                "discovered_at": time.time(), "next_attempt": 0,
            })
        seen[tweet.tweet_id] = tweet.published_at
    state["watermarks"][account] = max(watermark, *(int(t.tweet_id) for t in tweets))
    state["seen"][account] = dict(sorted(seen.items(), key=lambda kv: int(kv[0]))[-200:])
    if baseline:
        state["initialized_accounts"].append(account)
        LOG.info("initialized @%s with %d posts", account, len(tweets))
    save_state(path, state)  # Commit outbox BEFORE contacting a webhook.


def deliver_pending(state: dict[str, Any], routes: dict[str, list[WebhookTarget]], path: Path) -> bool:
    for key, item in list(state["pending"].items()):
        if STOP or item["next_attempt"] > time.time():
            continue
        tweet = Tweet(**item["tweet"])
        targets = {target_key(t): t for t in targets_for(tweet.account, routes)}
        for target_id in list(item["remaining"]):
            target = targets.get(target_id)
            if target is None:
                LOG.error("pending target removed from config for %s; retaining message", tweet.url)
                continue
            try:
                send_target(tweet, target)
            except Exception as exc:
                LOG.error("delivery failed for %s (%s): %s", tweet.url, target.kind, type(exc).__name__)
                continue
            item["remaining"].remove(target_id)
            save_state(path, state)  # Do not resend acknowledged targets on retry/restart.
            try:
                age = time.time() - datetime.fromisoformat(tweet.published_at.replace("Z", "+00:00")).timestamp()
                LOG.log(logging.WARNING if age > 300 else logging.INFO,
                        "delivered %s to %s; post age %.1fs", tweet.url, target.kind, age)
            except ValueError:
                LOG.info("delivered %s to %s; post age unknown", tweet.url, target.kind)
        if not item["remaining"]:
            del state["pending"][key]
        else:
            item["next_attempt"] = time.time() + 30
        save_state(path, state)
    return not state["pending"]


def collect_extra_account(page, account, metadata, state, routes, path, feed):
    """Use the same durable per-channel queue for new accounts as for core accounts."""
    tweets = scrape_account(page, account, max_items=8, navigation_ms=15000, cards_ms=5000, scrolls=2)
    ingest(state, account, tweets, routes, path)
    # Export is independent: a shared-feed problem must not lose user notifications.
    try:
        feed.success(account, tweets, metadata['interval_seconds'])
    except Exception as exc:
        LOG.error('public export failed for @%s: %s', account, type(exc).__name__)
    record_scrape_health(state, account, True, routes, path)
    deliver_pending(state, routes, path)


def handle_signal(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def required_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor X accounts without the paid X API")
    parser.add_argument("--login", action="store_true", help="open a headed browser for manual X login and save the session")
    parser.add_argument("--once", action="store_true", help="run one polling cycle and exit")
    parser.add_argument("--test-webhooks", action="store_true", help="send a test message to configured webhooks and exit")
    parser.add_argument("--health", action="store_true", help="check recent successful scrapes and pending message age")
    parser.add_argument("--check-config", action="store_true", help="validate environment, routes, and browser installation")
    return parser.parse_args()


def login_browser(profile_dir: Path) -> int:
    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("opening X login browser with profile %s", profile_dir)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        try:
            response = page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=45_000)
            if response and response.status >= 400:
                raise RuntimeError(f"X login page HTTP {response.status}; check network and X restrictions")
            print("请在浏览器里完成 X 登录，再回到终端按回车检查。", flush=True)
            input()
            if not page.locator('[data-testid="SideNav_AccountSwitcher_Button"]').count():
                raise RuntimeError("cannot verify login from page; check session manually and retry --login")
        finally:
            browser.close()
    LOG.info("login indicator detected; browser profile saved on this machine")
    return 0


def check_config(accounts: list[str], routes: dict[str, list[WebhookTarget]], profile_dir: Path) -> int:
    problems: list[str] = []
    if not accounts:
        problems.append("X_ACCOUNTS is empty")
    for account in accounts:
        if not re.fullmatch(r"[a-zA-Z0-9_]{1,15}", account):
            problems.append("invalid X username")
        if not targets_for(account, routes):
            problems.append(f"no webhook configured for @{account}")
    for target in [item for values in routes.values() for item in values] + global_targets():
        parsed = urlparse(target.url)
        if (any(token in target.url for token in ("REPLACE_ME", "GROUP_"))
                or parsed.scheme not in {"http", "https"} or not parsed.hostname):
            problems.append(f"invalid webhook URL for {target.kind}")
    if not profile_dir.parent.exists():
        problems.append(f"browser profile parent does not exist: {profile_dir.parent}")
    try:
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                problems.append("Playwright Chromium is not installed; run: python -m playwright install chromium")
    except Exception as exc:
        problems.append(f"Playwright unavailable: {exc}")
    if problems:
        for problem in problems:
            LOG.error("config: %s", problem)
        return 2
    LOG.info("config check passed for %d account(s), %d routed target(s), profile=%s", len(accounts), sum(len(v) for v in routes.values()) or len(global_targets()), profile_dir)
    return 0


def health_check(state_path: Path, accounts: list[str]) -> int:
    if not accounts or not state_path.exists():
        return 1
    state = load_state(state_path)
    now = time.time()
    health = state.get("health", {})
    if now - health.get("heartbeat", 0) > 300:
        return 1
    for account in accounts:
        if now - health.get("accounts", {}).get(account, {}).get("last_success", 0) > 300:
            return 1
    if any(now - item["discovered_at"] > 300 for item in state["pending"].values()):
        return 1
    return 0


def record_scrape_health(state: dict[str, Any], account: str, success: bool,
                         routes: dict[str, list[WebhookTarget]], path: Path) -> None:
    health = state.setdefault("health", {"accounts": {}})
    health["heartbeat"] = time.time()
    entry = health["accounts"].setdefault(account, {"failures": 0, "last_success": 0, "alerted": False, "last_alert": 0})
    recovery = success and entry["alerted"]
    if success:
        entry["last_success"] = time.time()
        entry["failures"] = 0
    else:
        entry["failures"] += 1
    needs_alert = entry["failures"] >= 3 and time.time() - entry["last_alert"] >= 1800
    if recovery or needs_alert:
        message = ("监控恢复：已重新读取该账号页面。" if recovery else
                   "监控异常：连续三次无法读取该账号页面，请检查 X 登录状态、网络和访问限制。当前不能保证五分钟内推送。")
        notice = Tweet(account, "health", f"https://x.com/{account}", message, datetime.now(timezone.utc).isoformat(), "监控状态")
        try:
            notify(notice, routes)
        except Exception:
            LOG.error("could not deliver monitoring health notice for @%s", account)
        entry["last_alert"] = time.time()
        entry["alerted"] = not recovery
    save_state(path, state)


def run() -> int:
    args = parse_args()
    accounts = [item.strip().lstrip("@").lower() for item in required_env("X_ACCOUNTS").split(",") if item.strip()]
    poll_seconds = max(60, int(required_env("POLL_SECONDS", "120")))
    profile_dir = Path(required_env("BROWSER_PROFILE_DIR", "./browser-profile")).expanduser()
    state_path = Path(required_env("STATE_FILE", "./data/state.json")).expanduser()
    routes_path = Path(required_env("ROUTES_FILE", "./routes.json")).expanduser()
    headless = env_bool("HEADLESS", False)
    max_items = max(1, int(required_env("MAX_ITEMS_PER_ACCOUNT", "20")))
    if args.login:
        return login_browser(profile_dir)
    if args.health:
        extra_file=os.getenv('X_EXTRA_ACCOUNTS_FILE','')
        extra_accounts=list(load_accounts(extra_file)) if extra_file else []
        return health_check(state_path, list(dict.fromkeys(accounts+extra_accounts)))
    routes = load_routes(routes_path)

    if args.check_config:
        return check_config(accounts, routes, profile_dir)

    if args.test_webhooks:
        targets = list(dict.fromkeys([t for values in routes.values() for t in values] + global_targets()))
        if not targets:
            raise RuntimeError("no webhook configured; test not run")
        account_list = ", ".join("@" + a for a in accounts) or "未指定账号"
        notify(Tweet("monitor", "test", "https://x.com", f"X 监控接入测试。监控账号：{account_list}；检查间隔：{poll_seconds}秒。本条为测试消息。",
                     datetime.now(timezone.utc).isoformat(), "接入测试"), {"*": targets})
        LOG.info("all %d webhook(s) acknowledged test", len(targets))
        return 0
    if check_config(accounts, routes, profile_dir):
        return 2
    state = load_state(state_path)
    registry_path = os.getenv('X_EXTRA_ACCOUNTS_FILE','')
    feed_path = os.getenv('X_PUBLIC_FEED_DB','')
    feed = PublicFeed(feed_path) if feed_path else None
    registry = load_accounts(registry_path) if registry_path else {}
    if feed:feed.set_registry(registry)
    if registry and feed is None:raise RuntimeError('X_PUBLIC_FEED_DB is required for extra account scheduling')
    if any(not targets_for(a,routes) for a in registry):raise RuntimeError('extra account has no notification target')

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            viewport={"width": 1440, "height": 1000},
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        LOG.info("monitor started for %s; interval=%ss; profile=%s", ", ".join(accounts), poll_seconds, profile_dir)
        try:
            while not STOP:
                cycle_start = time.monotonic()
                if registry_path:
                    try:
                        registry = load_accounts(registry_path)
                        if feed:feed.set_registry(registry)
                    except (OSError,ValueError,TypeError): LOG.error('invalid extra account registry; retaining last valid registry')
                cycle_ok = True
                deliver_pending(state, routes, state_path)
                for account in accounts:
                    if STOP:
                        break
                    try:
                        tweets = scrape_account(page, account, max_items=max_items)
                        ingest(state, account, tweets, routes, state_path)
                        if feed and account in registry:
                            try: feed.success(account,tweets,registry[account]['interval_seconds'])
                            except Exception as exc: LOG.error('public export failed: %s',type(exc).__name__)
                        record_scrape_health(state, account, True, routes, state_path)
                    except Exception as exc:
                        cycle_ok = False
                        LOG.error("check failed for @%s: %s", account, exc)
                        record_scrape_health(state, account, False, routes, state_path)
                        if feed and account in registry:
                            try: feed.failure(account,type(exc).__name__,registry[account]['interval_seconds'])
                            except Exception: LOG.error('public feed health write failed')
                        if page.is_closed():
                            raise RuntimeError("browser page closed; exiting for supervisor restart") from exc
                    deliver_pending(state, routes, state_path)
                if feed and not STOP:
                    extra_started=time.monotonic()
                    for account in feed.due(registry,exclude=accounts,limit=6):
                        if STOP or time.monotonic()-extra_started>=60 or time.monotonic()-cycle_start>=poll_seconds-20: break
                        try:
                            collect_extra_account(page,account,registry[account],state,routes,state_path,feed)
                        except Exception as exc:
                            cycle_ok = False
                            feed.failure(account,type(exc).__name__,registry[account]['interval_seconds'])
                            record_scrape_health(state,account,False,routes,state_path)
                            LOG.warning('extra account @%s failed: %s',account,type(exc).__name__)
                            if page.is_closed(): raise RuntimeError('browser page closed') from exc
                        deliver_pending(state,routes,state_path)
                if args.once:
                    return 0 if cycle_ok and not state["pending"] else 1
                elapsed = time.monotonic() - cycle_start
                if elapsed > poll_seconds:
                    LOG.warning("cycle took %.1fs, exceeding polling interval %ss", elapsed, poll_seconds)
                deadline = cycle_start + poll_seconds
                while not STOP and time.monotonic() < deadline:
                    deliver_pending(state, routes, state_path)
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
        finally:
            browser.close()
    LOG.info("monitor stopped")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (RuntimeError, ValueError) as exc:
        LOG.error("startup/runtime error: %s", exc)
        sys.exit(2)
