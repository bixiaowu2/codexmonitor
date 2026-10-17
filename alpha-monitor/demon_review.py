#!/usr/bin/env python3
"""每日复盘: 对比基线, 命中币重要异动时推送。只读。"""
import sys, json, time, concurrent.futures
from pathlib import Path
sys.path.insert(0, "/opt/alpha-radar")
from radar import PublicAPI, FUTURES, num
import urllib.request
import types
import dingtalk

DATA = Path("/var/lib/demon-radar")
STATE = DATA / "review_state.json"

def load_env():
    env = {}
    for line in Path("/etc/alpha-radar.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def secret(env, name):
    p = env.get(name + "_FILE")
    return Path(p).read_text().strip() if p else env.get(name, "").strip()

def tg(token, chat, text):
    body = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage",
                                 data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()

def dingtalk_send(env, text):
    if env.get("DINGTALK_ENABLED", "false").strip().lower() != "true":
        return
    cfg = types.SimpleNamespace(
        ding_webhook=secret(env, "DINGTALK_WEBHOOK"),
        ding_secret=secret(env, "DINGTALK_SECRET"),
        ding_keyword=env.get("DINGTALK_KEYWORD", "Alpha雷达").strip(),
    )
    if not cfg.ding_webhook:
        return
    dingtalk.send(cfg, text)

def snap(api, m):
    sym = m["symbol"]
    futs = m.get("futures", sym + "USDT")
    out = {"symbol": sym}
    try:
        fr = api.get(FUTURES + "/fapi/v1/fundingRate", {"symbol": futs, "limit": 3})
        if isinstance(fr, list) and fr:
            out["fr"] = num(fr[-1].get("fundingRate"))
    except Exception:
        pass
    try:
        kl = api.get(FUTURES + "/fapi/v1/klines", {"symbol": futs, "interval": "1d", "limit": 3})
        if isinstance(kl, list) and kl:
            out["price"] = num(kl[-1][4])
            prev = num(kl[-2][4])
            out["chg24h"] = (num(kl[-1][4]) / prev - 1) * 100 if prev else None
    except Exception:
        pass
    try:
        oih = api.get(FUTURES + "/futures/data/openInterestHist", {"symbol": futs, "period": "1d", "limit": 2})
        if isinstance(oih, list) and oih:
            out["oi"] = num(oih[-1].get("sumOpenInterestValue"))
    except Exception:
        pass
    return out

def main():
    env = load_env()
    token = secret(env, "TELEGRAM_BOT_TOKEN")
    chat = secret(env, "TELEGRAM_CHAT_ID")
    baseline = json.loads((DATA / "baseline.json").read_text())
    matches = json.loads((DATA / "matches.json").read_text())
    base = {m["symbol"]: m for m in baseline["matches"]}
    api = PublicAPI("/var/lib/demon-radar/review-cache", ttl=600)
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for r in pool.map(lambda m: snap(api, m), matches):
            rows.append(r)
    alerts = []
    summary = []
    for r in rows:
        sym = r["symbol"]
        b = base.get(sym, {})
        chg = r.get("chg24h")
        chg_s = "{:+.1f}".format(chg) if isinstance(chg, (int, float)) else "?"
        fr = r.get("fr")
        fr_s = "{:.4f}".format(fr * 100) if isinstance(fr, (int, float)) else "?"
        px = r.get("price")
        px_s = "{:.6g}".format(px) if isinstance(px, (int, float)) else "?"
        summary.append("• {}: {}  24h {}%  费率 {}%".format(sym, px_s, chg_s, fr_s))
        bp = b.get("price")
        cp = r.get("price")
        if bp and cp:
            chg = (cp / bp - 1) * 100
            if chg >= 30:
                alerts.append("🚀 {} 较基线暴涨 {:+.0f}% (基线 {} → 现 {})".format(sym, chg, bp, cp))
            elif chg <= -30:
                alerts.append("⚠️ {} 较基线暴跌 {:+.0f}% (基线 {} → 现 {})".format(sym, chg, bp, cp))
        bfr = b.get("fr")
        cfr = r.get("fr")
        if bfr is not None and cfr is not None and cfr < -0.001 and bfr >= -0.001:
            alerts.append("🚀 {} 资金费率转负 {:.3f}% (基线 {:.3f}%)".format(sym, cfr * 100, bfr * 100))
        boi = b.get("oi")
        coi = r.get("oi")
        if boi and coi and coi > boi * 1.5:
            alerts.append("📈 {} 持仓暴增 {:+.0f}% ({:.1f}M→{:.1f}M)".format(sym, (coi / boi - 1) * 100, boi / 1e6, coi / 1e6))
    ts = time.strftime("%m-%d %H:%M UTC", time.gmtime())
    body = "📋 妖币每日复盘 @ {}\n较基线 {}".format(ts, baseline["ts"])
    if alerts:
        body += "\n\n异动:\n" + "\n".join(alerts)
    body += "\n\n当前:\n" + "\n".join(summary)
    print(body)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if alerts or time.time() - state.get("last", 0) > 2 * 86400:
        if token and chat:
            try:
                tg(token, chat, body)
                print("telegram: 复盘已推")
            except Exception as e:
                print("tg 失败:", str(e)[:100])
        try:
            dingtalk_send(env, body)
            print("dingtalk: 复盘已推")
        except Exception as e:
            print("钉钉失败:", str(e)[:100])
        state["last"] = time.time()
        STATE.write_text(json.dumps(state))

    # 用完基线后把它推进到当天 —— 否则 baseline.json 是孤儿文件(没有任何程序写它),
    # 复盘会永远跟两天前比。同时写 oi 与 oi_notional 两个键, 上下游字段名不一致。
    try:
        cur = json.loads((DATA / "matches.json").read_text())
        top10 = {m["symbol"]: m.get("top10") for m in cur}
        snap_rows, seen = [], set()
        for r in rows:
            if r["symbol"] in seen:
                continue
            seen.add(r["symbol"])
            v = r.get("oi")
            snap_rows.append({"symbol": r["symbol"], "top10": top10.get(r["symbol"]),
                              "oi": v, "oi_notional": v, "fr": r.get("fr"),
                              "price": r.get("price"), "chg24h": r.get("chg24h")})
        # 最小间隔: 基线最多每 12h 推进一次。否则每次运行都推进, 基线贴着"现在",
        # 复盘就永远比不出变化(恒 0% → 贴文永远为空)。
        try:
            prev_ts = float(json.loads((DATA / "baseline.json").read_text()).get("ts_epoch") or 0)
        except Exception:
            prev_ts = 0
        if snap_rows and time.time() - prev_ts >= 12 * 3600:
            (DATA / "baseline.json").write_text(json.dumps(
                {"ts": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                 "ts_epoch": time.time(), "matches": snap_rows},
                ensure_ascii=False, indent=2))
            print(f"baseline: 已推进({len(snap_rows)} 只, 距上次 {int((time.time()-prev_ts)/3600)}h)")
        else:
            print(f"baseline: 未推进(距上次仅 {int((time.time()-prev_ts)/3600)}h, 不足 12h)")
    except Exception as e:
        print("baseline 更新失败:", str(e)[:100])

if __name__ == "__main__":
    main()
