"""Demo-only AI-assisted EA strategy gateway.

Holds strategy eligibility server-side. No direct trading credentials, order
endpoints, or live-account execution. Only a distinct demo EA can act on results.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from threading import RLock

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from live_bridge import _age, _get, _indicator_alignment, bridge_access, dashboard_access
from ai_macro_grounding import compile_grounded, GroundingUnavailable

router = APIRouter()
_lock = RLock()
_cache = {}
MIN_SCORE = 51  # Indicator alignment, NOT empirical win probability.
MAX_FRAME_AGE = 120
CACHE_SECONDS = 180  # Grounded research refresh; not on every VPS poll

class StrategyRequest(BaseModel):
    platform: str = Field(pattern="^(MT4|MT5)$")
    login: int = Field(gt=0)
    server: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=48, pattern=r"^[A-Za-z0-9._#-]+$")

def account_key(p: StrategyRequest):
    seed = f"{p.platform}|{p.server}|{p.login}"
    return hashlib.sha256(seed.encode()).hexdigest()[:20]

def tf_direction(frame):
    i = frame["indicators"]
    required = ["ema20", "ema50", "macd_main", "macd_signal"]
    if any(i.get(k) is None for k in required):
        return None
    ema = 1 if i["ema20"] > i["ema50"] else -1 if i["ema20"] < i["ema50"] else 0
    macd = 1 if i["macd_main"] > i["macd_signal"] else -1 if i["macd_main"] < i["macd_signal"] else 0
    close = frame["candles"][-1]["close"]
    price = 1 if close > i["ema20"] else -1 if close < i["ema20"] else 0
    vote = ema + macd + price
    return "BUY" if vote >= 2 else "SELL" if vote <= -2 else None

def signal_from_snapshot(key, symbol):
    """M5 scalp entry, M1 trigger and M15/H1 higher-timeframe trend filters."""
    item = _get(key)
    if _age(item) > MAX_FRAME_AGE:
        raise HTTPException(409, "Broker snapshot stale")
    now = time.time()
    frames = {v["frame"]["timeframe"]: v["frame"]
              for v in item["frame_map"].values()
              if v["frame"]["symbol"] == symbol and now-v["received_at"] <= MAX_FRAME_AGE}
    if not frames:
        raise HTTPException(409, "No fresh candles for symbol")
    score = _indicator_alignment(list(frames.values()))
    required = ("M1", "M5", "M15", "H1")
    risk_reasons = []
    if any(tf not in frames for tf in required):
        risk_reasons.append("M1/M5/M15/H1 candles required")
    # A trigger bar is only allowed once per closed M5 bar.
    bar = int(frames["M5"]["candles"][-1]["time"]) if "M5" in frames else 0
    if all(tf in frames for tf in required):
        for tf in ("M1", "M5", "M15", "H1"):
            if tf_direction(frames[tf]) != score["bias"]:
                risk_reasons.append(f"{tf} direction not aligned")
        i15 = frames["M15"]["indicators"]
        i5 = frames["M5"]["indicators"]
        adx = i15.get("adx14")
        atr = i5.get("atr14")
        rsi = i5.get("rsi14")
        if adx is None or adx < 20:
            risk_reasons.append("M15 ADX below 20 or missing")
        if atr is None or atr <= 0:
            risk_reasons.append("M5 ATR missing")
        if rsi is None:
            risk_reasons.append("M5 RSI missing")
        elif score["bias"] == "BUY" and not 52 <= rsi <= 72:
            risk_reasons.append("BUY requires M5 RSI 52-72")
        elif score["bias"] == "SELL" and not 28 <= rsi <= 48:
            risk_reasons.append("SELL requires M5 RSI 28-48")
    if "H4" in frames and tf_direction(frames["H4"]) not in (score["bias"], None):
        risk_reasons.append("H4 contradicts scalp trend")
    if score["confidence_pct"] < MIN_SCORE:
        risk_reasons.append("Indicator alignment below 51")
    return {"account_key": key, "symbol": symbol, "bar": bar,
            "bias": score["bias"], "confidence_pct": score["confidence_pct"],
            "technical_ok": not risk_reasons, "block_reasons": risk_reasons,
            "timeframes": list(frames.keys()),
            "score_type": "INDICATOR_ALIGNMENT_NOT_WIN_PROBABILITY"}

def build_plan(request: StrategyRequest, *, use_ai: bool, research_only: bool = False):
    key = account_key(request)
    item = _get(key)
    if (item["snapshot"]["platform"] != request.platform
            or item["snapshot"]["login"] != request.login
            or item["snapshot"]["server"] != request.server):
        raise HTTPException(403, "Account mismatch")
    plan = signal_from_snapshot(key, request.symbol)
    plan["mode"] = "DEMO_ACCOUNT_ONLY"
    plan["trade_eligible"] = False
    plan["ai_checked"] = False
    plan["server_demo_enabled"] = os.getenv("DEMO_EA_STRATEGY_ENABLED","").lower() == "true"
    plan["fundamental"] = {"status":"NOT_RESEARCHED", "source":"GEMINI_GOOGLE_SEARCH"}
    plan["fundamental_status"] = "NOT_RESEARCHED"
    plan["sources"] = []
    plan["reason"] = "Indikator MT4/MT5 sudah dibaca."
    plan["risk"] = "Riset fundamental belum dijalankan."
    plan["setup_status"] = "RESEARCH_ONLY"
    if not plan["server_demo_enabled"]:
        plan["block_reasons"].append("Railway demo EA strategy disabled")
    if not use_ai or (not plan["server_demo_enabled"] and not research_only):
        return plan
    if not plan["technical_ok"]:
        plan["fundamental_status"] = "TECHNICAL_BLOCK"
        plan["risk"] = "Konfirmasi indikator scalping belum lengkap."
        return plan
    now = time.time()
    cache_id = (key, request.symbol, plan["bar"], plan["bias"])
    with _lock:
        cached = _cache.get(cache_id)
    if cached and now < cached["expires"]:
        compiled = cached["result"]
    else:
        frames = [
            v["frame"] for v in item["frame_map"].values()
            if v["frame"]["symbol"] == request.symbol
            and now - v["received_at"] < MAX_FRAME_AGE
        ]
        try:
            compiled = compile_grounded(request.symbol, frames, plan["bias"])
        except GroundingUnavailable as exc:
            compiled = {
                "ai_confirmed":False, "news_clear":False,
                "fundamental_status":"UNVERIFIED",
                "fundamental_summary":"Fundamental belum dapat diverifikasi.",
                "reason":"Bias teknikal tersedia; AI belum memverifikasi berita.",
                "risk":str(exc)[:110], "sources": [],
                "method":"GEMINI_GOOGLE_SEARCH_GROUNDING"
            }
        with _lock:
            _cache[cache_id] = {"result":compiled, "expires":now + CACHE_SECONDS}
    plan["ai_checked"] = True
    plan["fundamental"] = {
        "status":compiled["fundamental_status"],
        "source":compiled["method"],
        "summary":compiled["fundamental_summary"],
    }
    plan["fundamental_status"] = compiled["fundamental_status"]
    plan["fundamental_summary"] = compiled["fundamental_summary"]
    plan["reason"] = compiled["reason"]
    plan["risk"] = compiled["risk"]
    plan["sources"] = compiled["sources"]
    plan["ai_concurs"] = bool(compiled["ai_confirmed"])
    if not compiled["ai_confirmed"]:
        plan["block_reasons"].append("AI fundamental not verified or contradicts technical")
    plan["trade_eligible"] = (plan["technical_ok"] and plan["ai_concurs"]
                              and plan["server_demo_enabled"] and not research_only)
    plan["setup_status"] = "DEMO_ELIGIBLE" if plan["trade_eligible"] else "NO_ENTRY"
    return plan

@router.post("/api/bridge/strategy", dependencies=[Depends(bridge_access)])
def bridge_strategy(payload: StrategyRequest):
    """Simple four-field text response for MQL4/MQL5. No order issued here."""
    try:
        p = build_plan(payload, use_ai=True)
    except HTTPException:
        return PlainTextResponse("BLOCK|NONE|0|0", status_code=200)
    # Strictly no sensitive account data in EA response.
    return PlainTextResponse(
        f"{'ALLOW' if p['trade_eligible'] else 'BLOCK'}|{p['bias']}|{p['confidence_pct']}|{p['bar']}",
        headers={"Cache-Control":"no-store"})

@router.post("/api/live/strategy-preview", dependencies=[Depends(dashboard_access)])
def strategy_preview(payload: StrategyRequest):
    """Dashboard only: show score, eligibility, reason. Never places orders."""
    return build_plan(payload, use_ai=False)


@router.post("/api/live/ai-compiled", dependencies=[Depends(dashboard_access)])
def ai_compiled_dashboard(payload: StrategyRequest):
    """AI chart+macro analysis, never an order. Runs with EA kill switch OFF."""
    return build_plan(payload, use_ai=True, research_only=True)
