import asyncio
import json
import os
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BINANCE_BASE = "https://api.binance.com"

TRACKED_PAIRS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT",
    "ADAUSDT","AVAXUSDT","DOTUSDT","LINKUSDT",
    "MATICUSDT","UNIUSDT","NEARUSDT","INJUSDT",
    "APTUSDT","ARBUSDT","OPUSDT","ATOMUSDT",
]

SYM_META = {
    "BTCUSDT":  {"symbol":"BTC",  "icon":"₿",  "vol":"high"},
    "ETHUSDT":  {"symbol":"ETH",  "icon":"Ξ",  "vol":"high"},
    "SOLUSDT":  {"symbol":"SOL",  "icon":"◎",  "vol":"high"},
    "BNBUSDT":  {"symbol":"BNB",  "icon":"B",  "vol":"high"},
    "ADAUSDT":  {"symbol":"ADA",  "icon":"₳",  "vol":"med"},
    "AVAXUSDT": {"symbol":"AVAX", "icon":"A",  "vol":"med"},
    "DOTUSDT":  {"symbol":"DOT",  "icon":"●",  "vol":"med"},
    "LINKUSDT": {"symbol":"LINK", "icon":"⬡",  "vol":"med"},
    "MATICUSDT":{"symbol":"MATIC","icon":"M",  "vol":"med"},
    "UNIUSDT":  {"symbol":"UNI",  "icon":"🦄", "vol":"med"},
    "NEARUSDT": {"symbol":"NEAR", "icon":"N",  "vol":"low"},
    "INJUSDT":  {"symbol":"INJ",  "icon":"I",  "vol":"low"},
    "APTUSDT":  {"symbol":"APT",  "icon":"A",  "vol":"med"},
    "ARBUSDT":  {"symbol":"ARB",  "icon":"R",  "vol":"med"},
    "OPUSDT":   {"symbol":"OP",   "icon":"O",  "vol":"med"},
    "ATOMUSDT": {"symbol":"ATOM", "icon":"⚛",  "vol":"med"},
}

market_data = {}
for pair, meta in SYM_META.items():
    market_data[meta["symbol"]] = {
        "price":0.0,"change24h":0.0,"change1h":0.0,
        "priceHistory":[],"vol":meta["vol"],"icon":meta["icon"]
    }

agent_state = {
    "running":False,"capital":0.0,"currentCapital":0.0,
    "position":None,"pnlHistory":[],"sessionStart":None,
    "sessionDuration":0,"config":{},"cooldowns":{},
    "tradeCount":0,"wins":0,"trades":[],"log":[],
}

async def fetch_binance_prices():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(f"{BINANCE_BASE}/api/v3/ticker/24hr")
            for t in res.json():
                meta = SYM_META.get(t["symbol"])
                if not meta: continue
                sym = meta["symbol"]
                price = float(t["lastPrice"])
                change24h = float(t["priceChangePercent"])
                hist = market_data[sym]["priceHistory"]
                hist.append(price)
                if len(hist) > 60: hist.pop(0)
                change1h = (price - hist[max(0,len(hist)-10)]) / hist[max(0,len(hist)-10)] * 100 if len(hist) > 2 else change24h * 0.08
                market_data[sym].update({"price":price,"change24h":change24h,"change1h":change1h})
                pos = agent_state["position"]
                if pos and pos["symbol"] == sym:
                    pos["currentPrice"] = price
                    if price > pos["highPrice"]: pos["highPrice"] = price
    except Exception as e:
        print(f"Binance error: {e}")

def get_sorted_market(vol_filter="high", top_n=10):
    result = []
    now_ms = datetime.now().timestamp() * 1000
    for sym, d in market_data.items():
        if d["price"] == 0: continue
        if vol_filter == "high" and d["vol"] != "high": continue
        if vol_filter == "med" and d["vol"] == "low": continue
        cd = agent_state["cooldowns"].get(sym)
        result.append({**d, "symbol":sym, "mom":d["change1h"], "inCooldown": cd is not None and now_ms < cd})
    result.sort(key=lambda x: x["mom"], reverse=True)
    return result[:top_n]

def add_log(type_, label, desc):
    agent_state["log"].insert(0, {"type":type_,"label":label,"desc":desc,"time":datetime.now().strftime("%H:%M:%S")})
    if len(agent_state["log"]) > 100: agent_state["log"].pop()

def unrealized_pnl():
    pos = agent_state["position"]
    if not pos: return 0.0
    return (pos["currentPrice"] - pos["entryPrice"]) / pos["entryPrice"] * pos["size"]

def enter_position(crypto):
    cfg = agent_state["config"]
    size = agent_state["currentCapital"] * cfg["posSize"]
    agent_state["currentCapital"] -= size
    agent_state["position"] = {"symbol":crypto["symbol"],"icon":crypto["icon"],"entryPrice":crypto["price"],"currentPrice":crypto["price"],"highPrice":crypto["price"],"size":size,"entryTime":datetime.now().isoformat()}
    add_log("buy","ACQUISTO",f"{crypto['symbol']} @ ${crypto['price']:.4f} | Mom: {crypto['mom']:+.2f}% | Size: ${size:.2f}")

def exit_position(reason, cur_price):
    pos = agent_state["position"]
    cfg = agent_state["config"]
    pnl = (cur_price - pos["entryPrice"]) / pos["entryPrice"] * pos["size"]
    pct = (cur_price - pos["entryPrice"]) / pos["entryPrice"] * 100
    agent_state["currentCapital"] += pos["size"] + pnl
    agent_state["tradeCount"] += 1
    if pnl > 0: agent_state["wins"] += 1
    agent_state["cooldowns"][pos["symbol"]] = (datetime.now().timestamp() + cfg["cooldown"] * 3600) * 1000
    add_log("sell", reason, f"{pos['symbol']} @ ${cur_price:.4f} | {pnl:+.2f}$ ({pct:+.2f}%)")
    agent_state["position"] = None

def scan_and_trade():
    if not agent_state["running"]: return
    cfg = agent_state["config"]
    pnl_val = agent_state["currentCapital"] - agent_state["capital"] + unrealized_pnl()
    agent_state["pnlHistory"].append({"t":(datetime.now().timestamp()-agent_state["sessionStart"])/60,"v":pnl_val})
    if len(agent_state["pnlHistory"]) > 500: agent_state["pnlHistory"].pop(0)
    elapsed_ms = (datetime.now().timestamp() - agent_state["sessionStart"]) * 1000
    if elapsed_ms >= agent_state["sessionDuration"]:
        agent_state["running"] = False
        if agent_state["position"]: exit_position("SESSIONE SCADUTA", agent_state["position"]["currentPrice"])
        add_log("info","FINE SESSIONE","Durata massima raggiunta."); return
    if agent_state["position"]:
        pos = agent_state["position"]
        cur = pos["currentPrice"]
        if cur <= pos["highPrice"] * (1 - cfg["trailStop"]): exit_position("TRAILING STOP", cur)
        elif cur >= pos["entryPrice"] * (1 + cfg["takeProfit"]): exit_position("TAKE PROFIT", cur)
    else:
        market = get_sorted_market(cfg.get("volFilter","high"), cfg.get("topN",10))
        candidate = next((c for c in market if not c["inCooldown"] and c["mom"] > 0.3), None)
        if candidate: enter_position(candidate)

async def background_loop():
    while True:
        await fetch_binance_prices()
        scan_and_trade()
        await asyncio.sleep(8)

@app.on_event("startup")
async def startup():
    asyncio.create_task(background_loop())

@app.get("/health")
def health():
    return {"status":"ok","binance":any(d["price"]>0 for d in market_data.values())}

@app.get("/status")
def get_status():
    unr = unrealized_pnl()
    pnl = agent_state["currentCapital"] - agent_state["capital"] + unr
    pct = pnl / agent_state["capital"] * 100 if agent_state["capital"] > 0 else 0
    wr = agent_state["wins"] / agent_state["tradeCount"] * 100 if agent_state["tradeCount"] > 0 else 0
    remaining = max(0, agent_state["sessionDuration"] - (datetime.now().timestamp() - (agent_state["sessionStart"] or 0)) * 1000) if agent_state["running"] else 0
    return {"running":agent_state["running"],"capital":agent_state["capital"],"currentCapital":agent_state["currentCapital"],"pnl":pnl,"pct":pct,"tradeCount":agent_state["tradeCount"],"winRate":wr,"position":agent_state["position"],"remainingMs":remaining,"pnlHistory":agent_state["pnlHistory"][-100:],"log":agent_state["log"][:30]}

@app.get("/market")
def get_market():
    cfg = agent_state["config"]
    return {"market": get_sorted_market(cfg.get("volFilter","high"), cfg.get("topN",12))}

@app.post("/start")
async def start_agent(body: dict):
    if agent_state["running"]: return {"error":"Already running"}
    cfg = body.get("config", {})
    capital = float(cfg.get("capital", 1000))
    agent_state.update({"running":True,"capital":capital,"currentCapital":capital,"position":None,"pnlHistory":[{"t":0,"v":0}],"sessionStart":datetime.now().timestamp(),"sessionDuration":int(cfg.get("sessionDuration",8))*3600000,"config":{"posSize":float(cfg.get("posSize",0.8)),"topN":int(cfg.get("topN",10)),"volFilter":cfg.get("volFilter","high"),"trailStop":float(cfg.get("trailStop",0.08)),"takeProfit":float(cfg.get("takeProfit",0.15)),"cooldown":int(cfg.get("cooldown",2)),"sessionDuration":int(cfg.get("sessionDuration",8))},"cooldowns":{},"tradeCount":0,"wins":0,"trades":[],"log":[]})
    add_log("info","AVVIO",f"${capital:.0f} USDT | Stop {float(cfg.get('trailStop',0.08))*100:.0f}% | TP {float(cfg.get('takeProfit',0.15))*100:.0f}% | CD {cfg.get('cooldown',2)}h")
    return {"ok":True}

@app.post("/stop")
def stop_agent():
    if not agent_state["running"]: return {"error":"Not running"}
    agent_state["running"] = False
    if agent_state["position"]: exit_position("STOP MANUALE", agent_state["position"]["currentPrice"])
    pnl = agent_state["currentCapital"] - agent_state["capital"]
    add_log("info","STOP",f"P&L finale: {pnl:+.2f}$")
    return {"ok":True,"pnl":pnl}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT",8000)))
