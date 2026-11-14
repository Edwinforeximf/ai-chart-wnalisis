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
from fundamental_gate import calendar_context, CalendarUnavailable

router = APIRouter()
_lock = RLock()
_cache = {}
MIN_SCORE = 51  # Indicator alignment, NOT empirical win probability.
MAX_FRAME_AGE = 120
CACHE_SECONDS = 3600  # Cache one confirmed AI review for an H1 candle

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

def _model_check(plan, frames, fundamental):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return False, "Gemini is not configured"
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,72}", model):
        return False, "Invalid Gemini model"
    # AI veto only; no AI-generated trade sizes or invented statistics.
    payload_data = {"symbol":plan["symbol"],"proposed_bias":plan["bias"],
                    "verified_calendar": fundamental,
                    "indicators":[{
                        "timeframe":f["timeframe"],
                        "last_closed_candle":f["candles"][-1],
                        "indicators":f["indicators"]
                    } for f in frames]}
    prompt = (
        "Anda analis scalping MT4/MT5 dengan konfirmasi kalender fundamental. "
        "Dilarang membuat berita/data baru. Data verified_calendar berasal dari "
        "kalender ekonomi, tidak menjamin arah harga. "
        "Jika teknikal bertentangan, tidak cukup data, atau recent releases berpotensi "
        "mengubah kondisi namun tidak bisa ditafsirkan, pilih NO_TRADE. "
        "Jawab JSON dengan decision BUY/SELL/NO_TRADE, fundamental_compatible boolean, "
        "reason maksimal 18 kata. fundamental_compatible berarti tidak ada risiko "
        "makro terverifikasi yang bertentangan, BUKAN konfirmasi kenaikan/penurunan harga "
        "atau probabilitas kemenangan. Jangan membuat angka atau target harga."
        + json.dumps(payload_data, ensure_ascii=False, separators=(",",":"))
    )

    body = json.dumps({"contents":[{"parts":[{"text":prompt}]}],
                       "generationConfig":{"responseMimeType":"application/json",
                                            "temperature":0.1,
                                            "maxOutputTokens":140}}).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=body, method="POST",
        headers={"Content-Type":"application/json", "x-goog-api-key":api_key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            result = json.load(r)
        response = json.loads(result["candidates"][0]["content"]["parts"][0]["text"])
        decision = response["decision"]
        compatible = response.get("fundamental_compatible") is True
        return decision == plan["bias"] and compatible, str(response.get("reason",""))[:100]
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return False, "Gemini unavailable / invalid response"

def build_plan(request: StrategyRequest, *, use_ai: bool):
    key = account_key(request)
    item = _get(key)
    if (item["snapshot"]["platform"] != request.platform
            or item["snapshot"]["login"] != request.login
            or item["snapshot"]["server"] != request.server):
        raise HTTPException(403, "Account mismatch")
    plan = signal_from_snapshot(key, request.symbol)
    plan["mode"] = "DEMO_ACCOUNT_ONLY"
    plan["demo_execution_requires_explicit_ea_opt_in"] = True
    plan["trade_eligible"] = False
    plan["ai_checked"] = False
    plan["server_demo_enabled"] = os.getenv("DEMO_EA_STRATEGY_ENABLED", "").lower() == "true"
    if not plan["server_demo_enabled"]:
        plan["block_reasons"].append("Railway demo strategy disabled")
    # Economic calendar gate is mandatory even on DEMO. No provider/key => BLOCK.
    fundamental = None
    try:
        fundamental = calendar_context(request.symbol)
        plan["fundamental"] = {
            "status": fundamental["status"], "source": fundamental["source"],
            "nearby_events": fundamental["news_blackout_events"],
            "recent_releases": fundamental["recent_high_impact"],
        }
        if not fundamental["can_open_new_position"]:
            plan["block_reasons"].append("Fundamental news blackout")
    except CalendarUnavailable as exc:
        plan["fundamental"] = {"status": "UNAVAILABLE", "reason": str(exc)}
        plan["block_reasons"].append("Verified fundamental calendar unavailable")
    if plan["block_reasons"] or not use_ai:
        return plan
    now = time.time()
    # Cache per bar and calendar state; never reuse confirmations across release regimes.
    recent_ids = tuple(e["event"]+e["date_utc"] for e in fundamental["recent_high_impact"])
    cache_id = (key, request.symbol, plan["bar"], plan["bias"],
                fundamental["status"], recent_ids)
    with _lock:
        cached = _cache.get(cache_id)
        if cached and now < cached["expires"]:
            ok, reason = cached["ok"], cached["reason"]
        else:
            ok, reason = _model_check(plan, [
                v["frame"] for v in item["frame_map"].values()
                if v["frame"]["symbol"] == request.symbol
                and now-v["received_at"] < MAX_FRAME_AGE], fundamental)
            _cache[cache_id] = {"ok": ok, "reason": reason,
                                "expires": now + (CACHE_SECONDS if ok else 120)}
    plan["ai_checked"] = True
    plan["ai_concurs"] = ok
    plan["ai_reason"] = reason
    plan["trade_eligible"] = bool(ok and fundamental["can_open_new_position"])
    if not ok:
        plan["block_reasons"].append("AI did not confirm fundamentals and technical bias")
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
