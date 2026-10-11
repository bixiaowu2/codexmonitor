#!/usr/bin/env python3
"""X 发帖器: 把三个雷达(妖币/meme/股票)的输出组装成推文, 经官方 API 发布。

三类内容(与推送体系一致):
  筛选类: 今日 S 级观察池清单
  信号类: 启动/回撤/止盈即时帖
  复盘类: 每日雷达复盘

防封设计(合规向):
  - 官方 API 免费档, 明牌自动化;
  - 每日发帖上限(默认 8) + 发布间隔随机抖动;
  - 内容全部为自有雷达数据, 无抓取/无互动自动化。
用法:
  x_publisher.py --test          发一条测试帖(验证链路)
  x_publisher.py --scan          立即组装+发布一条"筛选类"内容(若队列允许)
  x_publisher.py --queue FILE    把指定 JSONL 内容加入发布队列
  x_publisher.py --run           处理队列(由 cron/timer 调度)
"""
from __future__ import annotations
import sys, json, time, random, argparse, urllib.request
from pathlib import Path
from datetime import datetime, timezone

DATA = Path('/var/lib/x-publisher')
QUEUE = DATA / 'queue.jsonl'
STATE = DATA / 'state.json'
CREDS = Path('/etc/x-publisher/x_credentials.json')

DAILY_LIMIT = 8          # 每天最多发(免费档 1500/月, 留足余量)
MIN_GAP_SECONDS = 1800   # 两条最小间隔 30 分钟
MAX_GAP_SECONDS = 5400   # 抖动上限 90 分钟
MAX_TWEET_LEN = 280


def load_creds():
    d = json.loads(CREDS.read_text())
    return d['api_key'], d['api_secret'], d['access_token'], d['access_secret']


def get_api():
    import tweepy
    k, s, at, ats = load_creds()
    # v2 Client + OAuth1 user context: 免费档只有 POST /2/tweets
    return tweepy.Client(consumer_key=k, consumer_secret=s,
                         access_token=at, access_token_secret=ats,
                         wait_on_rate_limit=True)


def state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            return {}
    return {}


def save_state(st):
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False))


def can_post(st, now=None):
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')
    if st.get('post_date') != today:
        return True  # 新的一天
    if st.get('post_count', 0) >= DAILY_LIMIT:
        return False
    if now - st.get('last_post', 0) < MIN_GAP_SECONDS:
        return False
    return True


def post_tweet(text):
    api = get_api()
    if len(text) > MAX_TWEET_LEN:
        text = text[:MAX_TWEET_LEN - 1] + '…'
    resp = api.create_tweet(text=text)
    return resp.data['id']
def enqueue(text, kind='manual'):
    DATA.mkdir(parents=True, exist_ok=True)
    with QUEUE.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'kind': kind, 'text': text, 'created': time.time()}, ensure_ascii=False) + '\n')


def process_queue(limit=1):
    """每次最多发 limit 条(节奏控制); 成功后写入随机下次间隔。"""
    st = state()
    now = time.time()
    if not can_post(st, now):
        print(f"节奏限制: 今日已发 {st.get('post_count',0)}/{DAILY_LIMIT}, 上次距现在 {int((now-st.get('last_post',0))/60)} 分钟。跳过。")
        return False
    if not QUEUE.exists():
        print('队列为空')
        return False
    lines = [l for l in QUEUE.read_text(encoding='utf-8').splitlines() if l.strip()]
    if not lines:
        print('队列为空')
        return False
    item = json.loads(lines[0])
    rest = lines[1:]
    try:
        tid = post_tweet(item['text'])
        st['post_count'] = (st.get('post_count', 0) + 1) if st.get('post_date') == datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d') else 1
        st['post_date'] = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')
        st['last_post'] = now
        save_state(st)
        QUEUE.write_text('\n'.join(rest) + ('\n' if rest else ''))
        print(f"已发布 tweet_id={tid} kind={item['kind']}")
        return True
    except Exception as e:
        # 失败保留在队首, 记录错误(避免刷错内容)
        print(f"发布失败: {type(e).__name__}: {str(e)[:150]}")
        return False


# ---------- 内容组装(从雷达数据生成帖子) ----------

def compose_scan_alpha():
    """筛选类: 妖币观察池。"""
    try:
        m = json.loads(Path('/var/lib/demon-radar/matches.json').read_text())
    except Exception:
        return None
    sa = [x for x in m if x.get('tier') in ('S', 'A')]
    if not sa:
        return None
    lines = ['🚨 Alpha妖币观察池 | 数据筛选非投资建议',
             f'{time.strftime("%m-%d", time.gmtime())} S级大户控盘候选:']
    for x in sa[:3]:
        lines.append(f"▪ {x['symbol']} 集中度{x['cleaned_top10']*100:.0f}% 持仓{x['oi_notional']/1e6:.1f}M$")
    lines.append('逻辑: Alpha+合约+未上现货+高控盘, 临近现货窗口期')
    return '\n'.join(lines)


def compose_signal_alpha():
    """信号类: 从 track_state 读最近触发的信号(有新才发)。"""
    try:
        st = json.loads(Path('/var/lib/demon-radar/track_state.json').read_text())
    except Exception:
        return None
    sigs = st.get('recent_signals') or []
    if not sigs:
        return None
    s = sigs[-1]
    return s.get('text')  # 由 demon-track 写入


def compose_review():
    """复盘类: 今日基线对比。"""
    try:
        b = json.loads(Path('/var/lib/demon-radar/baseline.json').read_text())
        t = json.loads(Path('/var/lib/demon-radar/track_state.json').read_text())
    except Exception:
        return None
    rows = []
    for m in b.get('matches', [])[:5]:
        sym = m['symbol']
        oi = t.get('oi_snap', {}).get(sym)
        oi_s = f"{oi/1e6:.1f}M$" if oi else '?'
        rows.append(f"▪ {sym} 持仓{oi_s} 集中度{m.get('cleaned_top10',0)*100:.0f}%")
    lines = ['📊 Alpha雷达日复盘 | 研究记录',
             f'{time.strftime("%m-%d", time.gmtime())} S级持仓动态:']
    lines += rows
    lines.append('高控盘=主力布局, 非买入信号。瀑布有回撤预警。')
    return '\n'.join(lines)


def tg_send(text):
    """推草稿到 Telegram(人工复制发X)。"""
    import urllib.request
    env = {}
    for line in Path("/etc/alpha-radar.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("缺少 Telegram 配置")
        return False
    body = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()
    return True


def compose_meme():
    """筛选类: meme S+A 级。"""
    try:
        m = json.loads(Path("/var/lib/meme-radar/meme_matches.json").read_text())
    except Exception:
        return None
    if not m:
        return None
    lines = ["🌊 Meme雷达观察池 | 数据筛选非投资建议",
             time.strftime("%m-%d", time.gmtime()) + " 高分候选:"]
    for x in m[:3]:
        liq = x.get("liquidity_usd") or 0
        lines.append(f"▪ {x['symbol']}({x.get('chain','?')}) score {x.get('score',0):.0f} 流动性{liq/1000:.0f}K$")
    lines.append("逻辑: 安全通过+聪明钱+流动性, 早期观察")
    return "\n".join(lines)


def compose_stock():
    """筛选类: 股票 S 级(分市场)。"""
    try:
        m = json.loads(Path("/var/lib/stock-radar/stock_matches.json").read_text())
    except Exception:
        return None
    s_tier = [x for x in m if x.get("tier") == "S"]
    if not s_tier:
        return None
    lines = ["📈 股票雷达观察池 | 大股东控盘筛选",
             time.strftime("%m-%d", time.gmtime()) + " S级:"]
    for x in s_tier[:4]:
        conc = x.get("conc")
        conc_s = f"{conc:.0f}%" if conc else "?"
        lines.append(f"▪ {x.get('name','?')}({x.get('symbol','?')}) 大股东{conc_s}")
    lines.append("逻辑: 内部人+机构高度持仓, 等技术面确认")
    return "\n".join(lines)


def draft_today():
    """生成今日草稿包(只发币安Alpha相关, 定位为Alpha达人)。"""
    drafts = []
    for name, fn in (("妖币观察池", compose_scan_alpha),
                     ("每日复盘", compose_review)):
        try:
            text = fn()
        except Exception:
            text = None
        if text:
            drafts.append((name, text))
    return drafts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--test', action='store_true')
    ap.add_argument('--scan', action='store_true')
    ap.add_argument('--review', action='store_true')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--draft', action='store_true', help='生成今日草稿推文, 推送Telegram(人工发X)')
    args = ap.parse_args()

    if args.test:
        text = ('Alpha雷达上线测试 ✅\n'
                '本号将日更: 币安Alpha妖币观察池(大户控盘筛选)/启动·回撤信号/每日复盘\n'
                '数据筛选, 不构成投资建议。')
        try:
            tid = post_tweet(text)
            print(f"测试帖已发 tweet_id={tid}")
        except Exception as e:
            print(f"测试失败: {type(e).__name__}: {str(e)[:200]}")
        return

    if args.scan:
        text = compose_scan_alpha()
        if text:
            enqueue(text, 'scan')
            print('筛选类内容已入队')
        else:
            print('无筛选内容')
        return
    if args.review:
        text = compose_review()
        if text:
            enqueue(text, 'review')
            print('复盘类内容已入队')
        else:
            print('无复盘内容')
        return
    if args.run:
        process_queue()
        return

    if args.draft:
        drafts = draft_today()
        if not drafts:
            print("今日无可发内容")
            return
        ts = time.strftime("%m-%d %H:%M UTC", time.gmtime())
        parts = [f"📝 X发帖草稿 @ {ts}"]
        for name, text in drafts:
            parts.append("\n──── " + name + " ────\n" + text)
        body = "\n\n".join(parts)
        if tg_send(body):
            print(f"草稿已推 Telegram({len(drafts)}条), 请复制到X手动发布")
        return

    print(__doc__)


if __name__ == '__main__':
    main()
