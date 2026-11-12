"""Read-only MT4/MT5 bridge. No broker credentials, execution, or close orders."""
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

router = APIRouter()
_lock = RLock()
_snapshots = {}
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

def dashboard_access(authorization: str | None = Header(default=None)):
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    _check(token, "DASHBOARD_TOKEN", "dashboard")

def bridge_access(x_bridge_key: str | None = Header(default=None)):
    _check(x_bridge_key, "BRIDGE_TOKEN", "bridge")

def _account_key(snapshot: Snapshot) -> str:
    src = f"{snapshot.platform}|{snapshot.server}|{snapshot.login}"
    return hashlib.sha256(src.encode()).hexdigest()[:20]

def _age(item):
    return max(0, int(time.time() - item["received_at"]))

@router.get("/live", response_class=HTMLResponse)
def live_home():
    return (Path(__file__).parent / "live.html").read_text(encoding="utf-8")

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
    item = {"received_at": time.time(), "snapshot": parsed.model_dump()}
    with _lock:
        _snapshots[key] = item
    return {"received": True, "account_key": key, "mode": "READ_ONLY", "broker_orders_sent": 0}

@router.get("/api/live/accounts", dependencies=[Depends(dashboard_access)])
def live_accounts():
    with _lock:
        items = list(_snapshots.items())
    return {"accounts": [
        {"account_key": k, "platform": v["snapshot"]["platform"],
         "login": v["snapshot"]["login"], "broker": v["snapshot"]["broker"],
         "server": v["snapshot"]["server"], "currency": v["snapshot"]["currency"],
         "symbols": sorted({f["symbol"] for f in v["snapshot"]["frames"]}),
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
    return {"mode": "READ_ONLY", "age_seconds": _age(item),
            "fresh": _age(item) < MAX_AGE_SECONDS,
            **item["snapshot"]}

class AnalysisRequest(BaseModel):
    account_key: str
    symbol: str = Field(min_length=1, max_length=48)

@router.post("/api/live/analyze", dependencies=[Depends(dashboard_access)])
def live_analyze(body: AnalysisRequest):
    item = _get(body.account_key)
    if _age(item) >= MAX_AGE_SECONDS:
        raise HTTPException(409, "Stale broker data. Wait for a new EA snapshot")
    frames = [f for f in item["snapshot"]["frames"] if f["symbol"] == body.symbol]
    if len(frames) < 2:
        raise HTTPException(422, "Insufficient timeframe coverage")
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(503, "Set GEMINI_API_KEY in Railway to enable AI")
    safe_data = {
        "platform": item["snapshot"]["platform"],
        "symbol": body.symbol,
        "server_timeframes": [
            {"timeframe": f["timeframe"], "last_closed_candle": f["candles"][-1],
             "indicators": f["indicators"]} for f in frames
        ],
        "fundamental_feed": "NOT_CONNECTED"
    }
    prompt = (
        "Anda analis teknikal trading. Jawab Bahasa Indonesia dalam JSON saja. "
        "Hanya analisis data indikator native MetaTrader dan candle tertutup yang diberikan. "
        "Jangan mengarang berita, kalender ekonomi, harga live, yield, probabilitas keberhasilan, "
        "entry/SL/TP yang tak didukung, atau konfirmasi fundamental. "
        "Timeframe harus disebut. Return JSON object with fields "
        "decision (BUY/SELL/WAIT), summary (string), evidence (array of short strings), "
        "risks (array of short strings), fundamental_status (NOT_CONNECTED), "
        "disclaimer (string). "
        "Jika indikator berkonflik atau tidak cukup data, pilih WAIT. "
        "Ini laporan analisis read-only; tidak ada order yang boleh dijalankan.\n"
        + json.dumps(safe_data, ensure_ascii=False, separators=(",", ":"))
    )
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    if not re.fullmatch(r"[a-zA-Z0-9._-]{3,72}", model):
        raise HTTPException(500, "Invalid GEMINI_MODEL config")
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json",
                                               "temperature": 0.2, "maxOutputTokens": 1100}}).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            reply = json.load(response)
        text = reply["candidates"][0]["content"]["parts"][0]["text"]
        report = json.loads(text)
        if report.get("decision") not in ("BUY", "SELL", "WAIT"):
            raise ValueError("AI decision not recognized")
        report["fundamental_status"] = "NOT_CONNECTED"
        return {"mode": "AI_READ_ONLY", "provider": "Gemini", "model": model,
                "symbol": body.symbol, "analysis": report,
                "data_age_seconds": _age(item), "broker_order_placed": False}
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, KeyError, IndexError) as exc:
        # Do not echo provider errors, which may include confidential metadata.
        raise HTTPException(502, "AI provider failed or returned invalid output") from None
