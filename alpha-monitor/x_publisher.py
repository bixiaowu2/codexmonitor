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

def tg_send_photo(path, caption=""):
    """把配图直接发到 Telegram(sendPhoto), 方便长按保存后发X。"""
    import urllib.request, uuid
    env = {}
    for line in Path("/etc/alpha-radar.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    p = Path(path)
    if not p.exists():
        return False
    boundary = uuid.uuid4().hex
    fields = {"chat_id": chat}
    if caption:
        fields["caption"] = caption[:1000]
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{p.name}\"\r\n"
             f"Content-Type: image/png\r\n\r\n").encode()
    body += p.read_bytes() + b"\r\n"
    body += (f"--{boundary}--\r\n").encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
        return True
    except Exception as e:
        print("发图失败:", str(e)[:100])
        return False


def compose_scan_alpha():
    """筛选类(早间栏目): 钩子帖 + Thread跟帖, 配图 scan.png。"""
    try:
        import x_chart
        x_chart.chart_scan()
    except Exception:
        pass
    try:
        m = json.loads(Path('/var/lib/demon-radar/matches.json').read_text())
    except Exception:
        return None
    sa = [x for x in m if x.get('tier') in ('S', 'A')]
    if not sa:
        return None
    top = sa[0]
    # 钩子: 首句反差/结论
    hook = (f"链上Top10持仓 {top['cleaned_top10']*100:.0f}%——"
            f"{top['symbol']} 是主力控盘还是即将引爆?\n\n"
            f"今日Alpha妖币观察池 👇")
    main_tweet = (hook + "\n\n"
                  f"🥇 {top['symbol']} 集中度{top['cleaned_top10']*100:.0f}% 持仓{top['oi_notional']/1e6:.1f}M USDT\n"
                  + "\n".join(f"🥈 {x['symbol']} 集中度{x['cleaned_top10']*100:.0f}% 持仓{x['oi_notional']/1e6:.1f}M USDT"
                               for x in sa[1:3])
                  + "\n\n筛选: Alpha+合约+未上现货+大户控盘≥60%\n#BinanceAlpha #Crypto")
    thread2 = ("为什么盯这几个?\n"
               "▪ 上了币安合约 = 有真实资金博弈\n"
               "▪ 未上现货 = 现货上市前是资金布局窗口\n"
               "▪ 清洗LP后的大户集中度 = 主力控盘强度\n\n"
               "横盘越久, 拉起来越猛。附图为集中度与持仓对比。")
    return {"main": main_tweet, "thread": [thread2], "chart": "/var/lib/x-publisher/charts/scan.png",
            "label": "妖币观察池"}


def compose_review():
    """复盘类(晚间栏目): 谁涨了谁被打回原形。"""
    try:
        import x_chart
        x_chart.chart_review()
    except Exception:
        pass
    try:
        b = json.loads(Path('/var/lib/demon-radar/baseline.json').read_text())
        t = json.loads(Path('/var/lib/demon-radar/track_state.json').read_text())
    except Exception:
        return None
    ups, downs = [], []
    for m in b.get('matches', [])[:5]:
        sym = m['symbol']
        cur = t.get('oi_snap', {}).get(sym) or m.get('oi_notional') or 0
        base = m.get('oi_notional') or 0
        if base > 0:
            chg = (cur / base - 1) * 100
            if chg >= 5:
                ups.append(f"🔺 {sym} 持仓{chg:+.0f}% ({base/1e6:.1f}M→{cur/1e6:.1f}M USDT)")
            elif chg <= -5:
                downs.append(f"🔻 {sym} 持仓{chg:+.0f}% ({base/1e6:.1f}M→{cur/1e6:.1f}M USDT)")
    if not ups and not downs:
        return None
    hook = "今日Alpha雷达复盘: 谁在被主力加仓, 谁在被打回原形? 👇"
    main_tweet = (hook + "\n\n"
                  + ("\n".join(ups[:3]) + "\n" if ups else "")
                  + ("\n".join(downs[:3]) if downs else "")
                  + "\n\n持仓=资金态度, 不等于价格必涨\n#BinanceAlpha #Crypto")
    thread2 = ("复盘逻辑:\n"
               "▪ 持仓上升+价格横盘 = 主力悄悄布局\n"
               "▪ 持仓暴增+价格急拉 = 已启动, 注意波动\n"
               "▪ 持仓下降 = 资金撤退, 别接飞刀\n\n"
               "明日观察池见早帖。")
    return {"main": main_tweet, "thread": [thread2], "chart": "/var/lib/x-publisher/charts/review.png",
            "label": "每日复盘"}


def draft_today():
    """生成今日草稿包(只发币安Alpha相关, 栏目化+Thread)。"""
    drafts = []
    for fn in (compose_scan_alpha, compose_review):
        try:
            text = fn()
        except Exception:
            text = None
        if text:
            drafts.append(text)
    return drafts


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
        parts = [f"📝 X发帖草稿 @ {ts}(发X时附上图片)"]
        for d in drafts:
            parts.append("\n━━━ " + d['label'] + " ━━━")
            parts.append("【主帖】\n" + d['main'])
            if d.get('chart'):
                parts.append("【配图】" + d['chart'])
            for i, t in enumerate(d.get('thread', []), 1):
                parts.append(f"【跟帖{i}】\n" + t)
        body = "\n\n".join(parts)
        if tg_send(body):
            sent = 0
            for d in drafts:
                if d.get("chart") and tg_send_photo(d["chart"], caption=d["label"] + " 配图(发X时附上)"):
                    sent += 1
            print(f"草稿已推 Telegram({len(drafts)}个栏目+{sent}张图), 复制主帖+跟帖, 长按存图后发X")
        return

    print(__doc__)


if __name__ == '__main__':
    main()
