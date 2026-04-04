import asyncio
import os
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SYM_META = {
    "BTCUSDT":  {"s": "BTC",  "i": "BTC", "v": "high"},
    "ETHUSDT":  {"s": "ETH",  "i": "ETH", "v": "high"},
    "SOLUSDT":  {"s": "SOL",  "i": "SOL", "v": "high"},
    "BNBUSDT":  {"s": "BNB",  "i": "BNB", "v": "high"},
    "ADAUSDT":  {"s": "ADA",  "i": "ADA", "v": "med"},
    "AVAXUSDT": {"s": "AVAX", "i": "AVAX","v": "med"},
    "DOTUSDT":  {"s": "DOT",  "i": "DOT", "v": "med"},
    "LINKUSDT": {"s": "LINK", "i": "LINK","v": "med"},
    "UNIUSDT":  {"s": "UNI",  "i": "UNI", "v": "med"},
    "NEARUSDT": {"s": "NEAR", "i": "NEAR","v": "low"},
    "INJUSDT":  {"s": "INJ",  "i": "INJ", "v": "low"},
}

md = {}
for pair, meta in SYM_META.items():
    md[meta["s"]] = {"price": 0.0, "c24": 0.0, "c1": 0.0, "hist": [], "vol": meta["v"], "icon": meta["i"]}

st = {
    "running": False,
    "capital": 0.0,
    "current": 0.0,
    "position": None,
    "pnlH": [{"t": 0, "v": 0}],
    "start": None,
    "duration": 0,
    "cfg": {},
    "cd": {},
    "trades": 0,
    "wins": 0,
    "log": [],
}


def add_log(t, l, d):
    st["log"].insert(0, {"type": t, "label": l, "desc": d, "time": datetime.now().strftime("%H:%M:%S")})
    st["log"] = st["log"][:100]


def unr():
    p = st["position"]
    if not p:
        return 0.0
    return (p["cur"] - p["entry"]) / p["entry"] * p["size"]


async def fetch():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.binance.com/api/v3/ticker/24hr")
            for t in r.json():
                meta = SYM_META.get(t["symbol"])
                if not meta:
                    continue
                s = meta["s"]
                price = float(t["lastPrice"])
                c24 = float(t["priceChangePercent"])
                md[s]["hist"].append(price)
                if len(md[s]["hist"]) > 60:
                    md[s]["hist"].pop(0)
                h = md[s]["hist"]
                if len(h) > 2:
                    c1 = (price - h[max(0, len(h) - 10)]) / h[max(0, len(h) - 10)] * 100
                else:
                    c1 = c24 * 0.08
                md[s]["price"] = price
                md[s]["c24"] = c24
                md[s]["c1"] = c1
                p = st["position"]
                if p and p["sym"] == s:
                    p["cur"] = price
                    if price > p["high"]:
                        p["high"] = price
    except Exception as e:
        print("Binance error: " + str(e))


def get_market(vf="high", n=10):
    now = datetime.now().timestamp() * 1000
    result = []
    for s, d in md.items():
        if d["price"] == 0:
            continue
        if vf == "high" and d["vol"] != "high":
            continue
        if vf == "med" and d["vol"] == "low":
            continue
        in_cd = bool(st["cd"].get(s) and now < st["cd"][s])
        result.append({
            "symbol": s,
            "icon": d["icon"],
            "price": d["price"],
            "mom": d["c1"],
            "change24h": d["c24"],
            "vol": d["vol"],
            "inCooldown": in_cd,
        })
    result.sort(key=lambda x: -x["mom"])
    return result[:n]


def do_trade():
    running = st["running"]
    if not running:
        return
    cfg = st["cfg"]
    elapsed = (datetime.now().timestamp() - st["start"]) * 1000
    if elapsed >= st["duration"]:
        st["running"] = False
        if st["position"]:
            do_exit("FINE SESSIONE")
        add_log("info", "FINE", "Sessione scaduta")
        return
    pnl_val = st["current"] - st["capital"] + unr()
    st["pnlH"].append({"t": elapsed / 60000, "v": pnl_val})
    if len(st["pnlH"]) > 500:
        st["pnlH"].pop(0)
    if st["position"]:
        p = st["position"]
        cur = p["cur"]
        trail = p["high"] * (1 - cfg["trailStop"])
        tp = p["entry"] * (1 + cfg["takeProfit"])
        if cur <= trail:
            do_exit("TRAILING STOP")
        elif cur >= tp:
            do_exit("TAKE PROFIT")
    else:
        mk = get_market(cfg.get("volFilter", "high"), cfg.get("topN", 10))
        candidate = None
        for c in mk:
            if not c["inCooldown"] and c["mom"] > 0.3:
                candidate = c
                break
        if candidate:
            sz = st["current"] * cfg["posSize"]
            st["current"] -= sz
            st["position"] = {
                "sym": candidate["symbol"],
                "icon": candidate["icon"],
                "entry": candidate["price"],
                "cur": candidate["price"],
                "high": candidate["price"],
                "size": sz,
                "time": datetime.now().isoformat(),
            }
            add_log("buy", "ACQUISTO",
                    candidate["symbol"] + " @ $" + str(round(candidate["price"], 4)) +
                    " | Mom:" + str(round(candidate["mom"], 2)) + "%" +
                    " | Size:$" + str(round(sz, 2)))


def do_exit(reason):
    p = st["position"]
    cfg = st["cfg"]
    cur = p["cur"]
    pnl = (cur - p["entry"]) / p["entry"] * p["size"]
    pct = (cur - p["entry"]) / p["entry"] * 100
    st["current"] += p["size"] + pnl
    st["trades"] += 1
    if pnl > 0:
        st["wins"] += 1
    st["cd"][p["sym"]] = (datetime.now().timestamp() + cfg.get("cooldown", 2) * 3600) * 1000
    add_log("sell", reason,
            p["sym"] + " @ $" + str(round(cur, 4)) +
            " | " + str(round(pnl, 2)) + "$ (" + str(round(pct, 2)) + "%)")
    st["position"] = None


async def background_loop():
    while True:
        await fetch()
        do_trade()
        await asyncio.sleep(8)


@app.on_event("startup")
async def startup():
    asyncio.create_task(background_loop())


@app.get("/")
def root():
    return {"name": "CryptoAgent", "ok": True}


@app.get("/health")
def health():
    binance_ok = any(d["price"] > 0 for d in md.values())
    return {"status": "ok", "binance": binance_ok}


@app.get("/status")
def get_status():
    u = unr()
    pnl = st["current"] - st["capital"] + u
    pct = pnl / st["capital"] * 100 if st["capital"] > 0 else 0
    wr = st["wins"] / st["trades"] * 100 if st["trades"] > 0 else 0
    rem = 0
    if st["running"] and st["start"]:
        rem = max(0, st["duration"] - (datetime.now().timestamp() - st["start"]) * 1000)
    return {
        "running": st["running"],
        "capital": st["capital"],
        "currentCapital": st["current"],
        "pnl": pnl,
        "pct": pct,
        "tradeCount": st["trades"],
        "winRate": wr,
        "position": st["position"],
        "remainingMs": rem,
        "pnlHistory": st["pnlH"][-100:],
        "log": st["log"][:30],
    }


@app.get("/market")
def get_market_endpoint():
    vf = st["cfg"].get("volFilter", "high")
    return {"market": get_market(vf, 12)}


@app.post("/start")
async def start(body: dict):
    if st["running"]:
        return {"error": "Already running"}
    cfg = body.get("config", {})
    cap = float(cfg.get("capital", 1000))
    st["running"] = True
    st["capital"] = cap
    st["current"] = cap
    st["position"] = None
    st["pnlH"] = [{"t": 0, "v": 0}]
    st["start"] = datetime.now().timestamp()
    st["duration"] = int(cfg.get("sessionDuration", 8)) * 3600000
    st["cfg"] = {
        "posSize": float(cfg.get("posSize", 0.8)),
        "topN": int(cfg.get("topN", 10)),
        "volFilter": cfg.get("volFilter", "high"),
        "trailStop": float(cfg.get("trailStop", 0.08)),
        "takeProfit": float(cfg.get("takeProfit", 0.15)),
        "cooldown": int(cfg.get("cooldown", 2)),
    }
    st["cd"] = {}
    st["trades"] = 0
    st["wins"] = 0
    st["log"] = []
    add_log("info", "AVVIO",
            "$" + str(round(cap)) +
            " | Stop " + str(round(float(cfg.get("trailStop", 0.08)) * 100)) + "%" +
            " | TP " + str(round(float(cfg.get("takeProfit", 0.15)) * 100)) + "%")
    return {"ok": True}


@app.post("/stop")
def stop():
    if not st["running"]:
        return {"error": "Not running"}
    st["running"] = False
    if st["position"]:
        do_exit("STOP MANUALE")
    pnl = st["current"] - st["capital"]
    add_log("info", "STOP", "P&L: " + str(round(pnl, 2)) + "$")
    return {"ok": True, "pnl": pnl}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)