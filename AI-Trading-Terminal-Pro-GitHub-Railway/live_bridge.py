"""Read-only MT4/MT5 bridge. No broker credentials, execution, or close orders."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, Depends, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

router = APIRouter()
_lock = RLock()
_snapshots = {}
_pin_attempts = {}  # Failed PIN attempts by connecting IP.
_global_pin_failures = []
SESSION_SECONDS = 30 * 24 * 3600  # Remember this browser for 30 days
MAX_AGE_SECONDS = 120
TF = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]

class Candle(BaseModel):
    time: int = Field(gt=0)
    open: float
    high: float
    low: float
    close: float

class Frame(BaseModel):
    symbol: str = Field(min_length=1, max_length=48, pattern=r"^[A-Za-z0-9._#-]+$")
    timeframe: TF
    candles: list[Candle] = Field(min_length=30, max_length=200)
    indicators: dict[str, float | None] = Field(default_factory=dict, max_length=32)

class Position(BaseModel):
    ticket: int = Field(ge=0)
    symbol: str = Field(max_length=48)
    side: Literal["BUY", "SELL"]
    volume: float = Field(ge=0)
    profit: float

class HistoryPosition(Position):
    closed_at: int | None = None

class Snapshot(BaseModel):
    platform: Literal["MT4", "MT5"]
    login: int = Field(gt=0)
    server: str = Field(min_length=1, max_length=120)
    broker: str = Field(max_length=120)
    currency: str = Field(min_length=3, max_length=8)
    balance: float
    equity: float
    margin: float
    free_margin: float
    positions: list[Position] = Field(default_factory=list, max_length=100)
    history: list[HistoryPosition] = Field(default_factory=list, max_length=100)
    frames: list[Frame] = Field(min_length=1, max_length=12)

def _check(candidate: str | None, expected: str, kind: str):
    token = os.getenv(expected, "")
    if not token or len(token) < 24:
        raise HTTPException(503, f"{kind} integration not configured")
    if not candidate or not hmac.compare_digest(candidate, token):
        raise HTTPException(401, f"Invalid {kind} token")

def _session_secret() -> bytes:
    # Reuse the existing server-only, high-entropy secret. It is never sent to the browser.
    secret = os.getenv("DASHBOARD_SESSION_SECRET") or os.getenv("DASHBOARD_TOKEN", "")
    if len(secret) < 24:
        raise HTTPException(503, "Set DASHBOARD_SESSION_SECRET (24+ random characters) in Railway")
    return secret.encode("utf-8")


def _issue_session(now: int) -> str:
    expires = now + SESSION_SECONDS
    payload = f"v1.{expires}.{secrets.token_urlsafe(24)}"
    sig = hmac.new(_session_secret(), payload.encode("ascii"), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{payload}.{signature}"


def _verify_session(cookie: str) -> bool:
    try:
        version, exp, nonce, sig = cookie.split(".")
        if version != "v1" or len(cookie) > 256:
            return False
        expires = int(exp)
        now = int(time.time())
        if not (now < expires <= now + SESSION_SECONDS):
            return False
        payload = f"{version}.{exp}.{nonce}"
        expected = hmac.new(_session_secret(), payload.encode("ascii"), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        return hmac.compare_digest(expected, actual)
    except (ValueError, TypeError, UnicodeError):
        return False


def dashboard_access(request: Request, authorization: str | None = Header(default=None)):
    """Read-only browser session or server-side legacy bearer for API clients."""
    cookie = request.cookies.get("tt_session", "")
    if cookie and _verify_session(cookie):
        return
    bearer = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    expected = os.getenv("DASHBOARD_TOKEN", "")
    if bearer and len(expected) >= 24 and hmac.compare_digest(bearer, expected):
        return
    raise HTTPException(401, "Dashboard PIN login required")


class PinLogin(BaseModel):
    pin: str = Field(pattern=r"^[0-9]{4}$")


@router.post("/api/live/login")
def pin_login(body: PinLogin, request: Request, response: Response):
    """Four-digit PIN is for read-only dashboard convenience, not order authorization."""
    expected = os.getenv("DASHBOARD_PIN", "")
    if not re.fullmatch(r"[0-9]{4}", expected):
        raise HTTPException(503, "Set DASHBOARD_PIN (4 digits) in Railway Variables")
    now = time.time()
    ip = request.client.host if request.client else "unknown"
    with _lock:
        failures = [t for t in _pin_attempts.get(ip, []) if now - t < 900]
        global_failures = [t for t in _global_pin_failures if now - t < 900]
        _pin_attempts[ip] = failures
        _global_pin_failures[:] = global_failures
        if len(failures) >= 5 or len(global_failures) >= 40:
            raise HTTPException(429, "Too many PIN attempts. Try again later.")
        if not hmac.compare_digest(body.pin, expected):
            failures.append(now)
            global_failures.append(now)
            raise HTTPException(401, "Incorrect dashboard code")
        _pin_attempts.pop(ip, None)
        token = _issue_session(int(now))
    response.set_cookie("tt_session", token, max_age=SESSION_SECONDS, httponly=True,
                        secure=True, samesite="strict", path="/api/live")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "expires_in_seconds": SESSION_SECONDS,
            "mode": "READ_ONLY"}


@router.get("/api/live/session")
def pin_session(_: None = Depends(dashboard_access)):
    return {"authenticated": True, "mode": "READ_ONLY"}


@router.post("/api/live/logout")
def pin_logout(request: Request, response: Response):
    # Logout removes the cookie from this browser; rotate the server signing
    # secret to invalidate all active devices if needed.
    response.delete_cookie("tt_session", path="/api/live", secure=True, httponly=True,
                           samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"logged_out": True}

def bridge_access(x_bridge_key: str | None = Header(default=None)):
    _check(x_bridge_key, "BRIDGE_TOKEN", "bridge")

def _account_key(snapshot: Snapshot) -> str:
    src = f"{snapshot.platform}|{snapshot.server}|{snapshot.login}"
    return hashlib.sha256(src.encode()).hexdigest()[:20]

def _age(item):
    return max(0, int(time.time() - item["received_at"]))

@router.get("/live", response_class=HTMLResponse)
def live_home():
    return HTMLResponse(
        content=(Path(__file__).parent / "live.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 "Pragma": "no-cache", "Expires": "0"}
    )

@router.get("/api/live/status", dependencies=[Depends(dashboard_access)])
def live_status():
    with _lock:
        items = list(_snapshots.values())
    return {"mode": "READ_ONLY", "accounts": len(items),
            "fresh_accounts": sum(_age(x) < MAX_AGE_SECONDS for x in items),
            "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
            "broker_execution_enabled": False,
            "storage": "MEMORY_ONLY_RESETS_ON_REDEPLOY"}

@router.post("/api/bridge/push", dependencies=[Depends(bridge_access)])
async def bridge_push(request: Request):
    if int(request.headers.get("content-length") or 0) > 800_000:
        raise HTTPException(413, "Payload too large")
    raw = await request.body()
    if len(raw) > 800_000:
        raise HTTPException(413, "Payload too large")
    try:
        parsed = Snapshot.model_validate_json(raw)
    except Exception:
        raise HTTPException(422, "Invalid bridge payload")
    # Never accept broker credentials in this endpoint.
    key = _account_key(parsed)
    now = time.time()
    with _lock:
        previous = _snapshots.get(key, {})
        frame_map = {k: v for k, v in previous.get("frame_map", {}).items()
                     if now - v["received_at"] < MAX_AGE_SECONDS}
        for frame in parsed.frames:
            frame_map[(frame.symbol, frame.timeframe)] = {
                "received_at": now, "frame": frame.model_dump()}
        _snapshots[key] = {"received_at": now, "snapshot": parsed.model_dump(exclude={"frames"}),
                           "frame_map": frame_map}
    return {"received": True, "account_key": key, "mode": "READ_ONLY",
            "broker_orders_sent": 0, "fresh_frames": len(frame_map)}

@router.get("/api/live/accounts", dependencies=[Depends(dashboard_access)])
def live_accounts():
    with _lock:
        items = list(_snapshots.items())
    return {"accounts": [
        {"account_key": k, "platform": v["snapshot"]["platform"],
         "login": v["snapshot"]["login"], "broker": v["snapshot"]["broker"],
         "server": v["snapshot"]["server"], "currency": v["snapshot"]["currency"],
         "symbols": sorted({f["frame"]["symbol"] for f in v["frame_map"].values()
                            if time.time()-f["received_at"] < MAX_AGE_SECONDS}),
         "age_seconds": _age(v), "fresh": _age(v) < MAX_AGE_SECONDS}
        for k, v in items
    ]}

def _get(key):
    with _lock:
        item = _snapshots.get(key)
    if item is None:
        raise HTTPException(404, "No snapshot for account")
    return item

@router.get("/api/live/snapshot/{key}", dependencies=[Depends(dashboard_access)])
def live_snapshot(key: str):
    item = _get(key)
    frames = [v["frame"] for v in item["frame_map"].values()
              if time.time()-v["received_at"] < MAX_AGE_SECONDS]
    return {"mode": "READ_ONLY", "age_seconds": _age(item),
            "fresh": _age(item) < MAX_AGE_SECONDS, **item["snapshot"],
            "frames": sorted(frames, key=lambda f: (f["symbol"], f["timeframe"]))}

class AnalysisRequest(BaseModel):
    account_key: str
    symbol: str = Field(min_length=1, max_length=48)

def _indicator_alignment(frames):
    """Rule-based directional agreement score; never a win probability."""
    tf_weight = {"M1": 0.45, "M5": 0.6, "M15": 0.85, "M30": 1.0,
                 "H1": 1.3, "H4": 1.6, "D1": 1.8, "W1": 1.2, "MN1": 1.2}
    signed = 0.0
    available = 0.0

    def vote(a, b, weight):
        nonlocal signed, available
        if a is not None and b is not None:
            signed += weight * (1 if a > b else -1 if a < b else 0)
            available += weight

    for frame in frames:
        w = tf_weight.get(frame["timeframe"], 1.0)
        ind = frame["indicators"]
        close = frame["candles"][-1]["close"]
        vote(ind.get("ema20"), ind.get("ema50"), 2.0*w)
        vote(close, ind.get("ema20"), 1.1*w)
        vote(ind.get("macd_main"), ind.get("macd_signal"), 1.3*w)
        vote(ind.get("di_plus"), ind.get("di_minus"), 1.1*w)
        rsi = ind.get("rsi14")
        if rsi is not None:
            signed += (1 if rsi > 52 else -1 if rsi < 48 else 0) * 0.8*w
            available += 0.8*w
    if available < 1:
        raise HTTPException(422, "Insufficient native indicator data")
    bias = "BUY" if signed >= 0 else "SELL"
    # Scores measure indicator agreement only. Do not interpret as trade win probability.
    confidence = round(50 + 40 * min(1, abs(signed) / available))
    return {"bias": bias, "confidence_pct": confidence,
            "score_type": "INDICATOR_ALIGNMENT_NOT_WIN_PROBABILITY",
            "setup_status": "SETUP_OK" if confidence >= 51 else "LOW_CONFIDENCE_NO_ENTRY"}


@router.post("/api/live/analyze", dependencies=[Depends(dashboard_access)])
def live_analyze(body: AnalysisRequest):
    item = _get(body.account_key)
    if _age(item) >= MAX_AGE_SECONDS:
        raise HTTPException(409, "Stale broker data. Wait for a new EA snapshot")
    frames = [v["frame"] for v in item["frame_map"].values()
              if v["frame"]["symbol"] == body.symbol
              and time.time()-v["received_at"] < MAX_AGE_SECONDS]
    if len(frames) < 2:
        raise HTTPException(422, "Insufficient timeframe coverage")
    score = _indicator_alignment(frames)
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(503, "Set GEMINI_API_KEY in Railway to enable AI")
    safe_data = {
        "symbol": body.symbol,
        "technical_bias": score["bias"],
        "confidence_alignment_score": score["confidence_pct"],
        "timeframes": [
            {"timeframe": f["timeframe"], "last_closed_candle": f["candles"][-1],
             "indicators": f["indicators"]} for f in frames
        ],
        "fundamentals": "NOT_CONNECTED",
    }
    prompt = (
        "Anda analis teknikal. Jawab hanya JSON dengan tepat dua field string: "
        "reason dan risk. Reason maksimal 18 kata, risk maksimal 15 kata. "
        "Gunakan Bahasa Indonesia yang jelas dan ringkas, sebut indikator yang relevan. "
        "Jelaskan bias yang sudah dihitung dari indikator. Jangan buat perkiraan "
        "probabilitas kemenangan, jangan buat berita fundamental, harga baru, TP/SL, "
        "atau ajakan entry. Sebut konflik antar timeframe jika ada. "
        "Tidak boleh ada markdown, paragraf panjang atau daftar. "
        + json.dumps(safe_data, ensure_ascii=False, separators=(",", ":"))
    )
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    if not re.fullmatch(r"[a-zA-Z0-9._-]{3,72}", model):
        raise HTTPException(500, "Invalid GEMINI_MODEL config")
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json",
                                               "temperature": 0.2,
                                               "maxOutputTokens": 300}}).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=25) as reply:
            result = json.load(reply)
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError("Invalid AI response")
        reason = " ".join(str(obj.get("reason", "")).split())[:160]
        risk = " ".join(str(obj.get("risk", "")).split())[:120]
        if not reason or not risk:
            raise ValueError("Missing AI explanation")
        return {"mode": "AI_READ_ONLY", "provider": "Gemini", "model": model,
                "symbol": body.symbol, **score,
                "reason": reason, "risk": risk,
                "fundamental_status": "NOT_CONNECTED",
                "data_age_seconds": _age(item), "broker_order_placed": False}
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError,
            KeyError, IndexError, TypeError):
        raise HTTPException(502, "AI provider failed or returned invalid output") from None
