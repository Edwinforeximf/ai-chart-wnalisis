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

router = APIRouter()
_lock = RLock()
_cache = {}
MIN_SCORE = 51  # Indicator alignment, NOT empirical win probability.
MAX_FRAME_AGE = 120
CACHE_SECONDS = 300

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
    item = _get(key)
    if _age(item) > MAX_FRAME_AGE:
        raise HTTPException(409, "Broker snapshot stale")
    now = time.time()
    frames = {v["frame"]["timeframe"]: v["frame"]
              for v in item["frame_map"].values()
              if v["frame"]["symbol"] == symbol and now - v["received_at"] <= MAX_FRAME_AGE}
    if not frames:
        raise HTTPException(409, "No fresh candles for symbol")
    # The dashboard may still show BUY/SELL bias even if no entry is justified.
    score = _indicator_alignment(list(frames.values()))
    h1, h4, m15 = (frames.get(tf) for tf in ("H1", "H4", "M15"))
    bar = int(h1["candles"][-1]["time"]) if h1 else 0
    risk_reasons = []
    if not (h1 and h4 and m15):
        risk_reasons.append("M15/H1/H4 data incomplete")
    if h1 and h4:
        for tf, f in (("H1", h1), ("H4", h4)):
            if tf_direction(f) != score["bias"]:
                risk_reasons.append(f"{tf} not aligned")
        adx = h1["indicators"].get("adx14")
        atr = h1["indicators"].get("atr14")
        if adx is None or adx < 18:
            risk_reasons.append("H1 ADX < 18 / unavailable")
        if atr is None or atr <= 0:
            risk_reasons.append("H1 ATR missing")
    if score["confidence_pct"] < MIN_SCORE:
        risk_reasons.append("Score below 51")
    return {"account_key":key, "symbol":symbol, "bar":bar,
            "bias":score["bias"], "confidence_pct":score["confidence_pct"],
            "technical_ok":not risk_reasons, "block_reasons":risk_reasons,
            "fundamental":"NOT_CONNECTED",
            "score_type":"INDICATOR_ALIGNMENT_NOT_WIN_PROBABILITY"}

def _model_check(plan, frames):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return False, "Gemini is not configured"
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,72}", model):
        return False, "Invalid Gemini model"
    # AI veto only; no AI-generated trade sizes or invented statistics.
    payload_data = {"symbol":plan["symbol"],"proposed_bias":plan["bias"],
                    "indicators":[{
                        "timeframe":f["timeframe"],
                        "last_closed_candle":f["candles"][-1],
                        "indicators":f["indicators"]
                    } for f in frames]}
    prompt = (
        "Anda analis teknikal MetaTrader. Tinjau sinyal BUY/SELL yang dihitung aturan."
        " Hanya data closed bar yang tersedia. Tidak ada feed berita fundamental."
        " Jawab JSON dengan fields decision (BUY, SELL, atau NO_TRADE) dan"
        " reason (maksimum 12 kata). Jangan mengarang peluang menang, berita,"
        " stop loss, atau harga. Bila bukti saling bertentangan, NO_TRADE."
        " Jika keputusan berbeda dari proposed_bias, sistem tidak akan trading."
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
        return decision == plan["bias"], str(response.get("reason",""))[:100]
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return False, "Gemini unavailable / invalid response"

def build_plan(request: StrategyRequest, *, use_ai: bool):
    key = account_key(request)
    item = _get(key)
    if item["snapshot"]["platform"] != request.platform or item["snapshot"]["login"] != request.login or item["snapshot"]["server"] != request.server:
        raise HTTPException(403, "Account mismatch")
    plan = signal_from_snapshot(key, request.symbol)
    plan["mode"] = "DEMO_ACCOUNT_ONLY"
    plan["demo_execution_requires_explicit_ea_opt_in"] = True
    plan["trade_eligible"] = False
    plan["ai_checked"] = False
    if not plan["technical_ok"] or not use_ai:
        return plan
    now = time.time()
    cache_id = (key, request.symbol, plan["bar"], plan["bias"])
    with _lock:
        cached = _cache.get(cache_id)
        if cached and now < cached["expires"]:
            ok, reason = cached["ok"], cached["reason"]
        else:
            ok, reason = _model_check(plan, [
                v["frame"] for v in item["frame_map"].values()
                if v["frame"]["symbol"] == request.symbol
                and now-v["received_at"] < MAX_FRAME_AGE])
            _cache[cache_id] = {"ok":ok, "reason":reason,
                                "expires":now+(CACHE_SECONDS if ok else 120)}
    plan["ai_checked"] = True
    plan["ai_concurs"] = ok
    plan["ai_reason"] = reason
    plan["trade_eligible"] = bool(ok and plan["technical_ok"])
    if not ok:
        plan["block_reasons"].append("AI did not confirm the bias")
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
