import asyncio, os
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx, uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SYM_META = {"BTCUSDT":{"s":"BTC","i":"₿","v":"high"},"ETHUSDT":{"s":"ETH","i":"Ξ","v":"high"},"SOLUSDT":{"s":"SOL","i":"◎","v":"high"},"BNBUSDT":{"s":"BNB","i":"B","v":"high"},"ADAUSDT":{"s":"ADA","i":"₳","v":"med"},"AVAXUSDT":{"s":"AVAX","i":"A","v":"med"},"DOTUSDT":{"s":"DOT","i":"●","v":"med"},"LINKUSDT":{"s":"LINK","i":"⬡","v":"med"},"UNIUSDT":{"s":"UNI","i":"🦄","v":"med"},"NEARUSDT":{"s":"NEAR","i":"N","v":"low"},"INJUSDT":{"s":"INJ","i":"I","v":"low"}}

md = {m["s"]:{"price":0,"c24":0,"c1":0,"hist":[],"vol":m["v"],"icon":m["i"]} for m in SYM_META.values()}
st = {"running":False,"capital":0,"current":0,"position":None,"pnlH":[{"t":0,"v":0}],"start":None,"duration":0,"cfg":{},"cd":{},"trades":0,"wins":0,"log":[]}

def log(t,l,d): st["log"].insert(0,{"type":t,"label":l,"desc":d,"time":datetime.now().strftime("%H:%M:%S")}); st["log"]=st["log"][:100]

def unr():
    p=st["position"]
    return 0 if not p else (p["cur"]-p["entry"])/p["entry"]*p["size"]

async def fetch():
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r=await c.get("https://api.binance.com/api/v3/ticker/24hr")
            for t in r.json():
                m=SYM_META.get(t["symbol"])
                if not m: continue
                s=m["s"]; price=float(t["lastPrice"]); c24=float(t["priceChangePercent"])
                md[s]["hist"].append(price)
                if len(md[s]["hist"])>60: md[s]["hist"].pop(0)
                h=md[s]["hist"]; c1=(price-h[max(0,len(h)-10)])/h[max(0,len(h)-10)]*100 if len(h)>2 else c24*0.08
                md[s].update({"price":price,"c24":c24,"c1":c1})
                p=st["position"]
                if p and p["sym"]==s:
                    p["cur"]=price
                    if price>p["high"]: p["high"]=price
    except Exception as e: print(f"Binance error: {e}")

def market(vf="high",n=10):
    now=datetime.now().timestamp()*1000
    r=[{"symbol":s,"icon":d["icon"],"price":d["price"],"mom":d["c1"],"change24h":d["c24"],"vol":d["vol"],"inCooldown":bool(st["cd"].get(s) and now<st["cd"][s])} for s,d in md.items() if d["price"]>0 and not(vf=="high" and d["vol"]!="high") and not(vf=="med" and d["vol"]=="low")]
    return sorted(r,key=lambda x:-x["mom"])[:n]

def trade():
    if not st["running​​​​​​​​​​​​​​​​
