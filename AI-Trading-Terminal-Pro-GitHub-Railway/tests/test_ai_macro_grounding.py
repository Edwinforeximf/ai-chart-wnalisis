import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_macro_grounding import compile_grounded, GroundingUnavailable

NOW=datetime(2026,11,14,11,0,tzinfo=timezone.utc)

def frame(tf):
    return {"timeframe":tf,
            "candles":[{"time":1794600000,"open":2700,"high":2702,"low":2698,"close":2701}],
            "indicators":{"ema20":2699,"ema50":2698,"rsi14":62,
                          "macd_main":1.2,"macd_signal":0.9}}

def response(last_minutes=180, next_minutes=240, citations=True, bias="BUY"):
    report={"decision":bias,"macro_bias":bias,
            "reason":"EMA dan konteks makro selaras.",
            "risk":"Waspadai volatilitas mendadak.",
            "fundamental_summary":"Berita makro USD sesuai dengan bias tren.",
            "last_high_impact_utc":(NOW-timedelta(minutes=last_minutes)).isoformat(),
            "next_high_impact_utc":(NOW+timedelta(minutes=next_minutes)).isoformat()}
    return {"candidates":[{"content":{"parts":[{"text":json.dumps(report)}]},
                           "groundingMetadata":{
                             "webSearchQueries":["latest US CPI release Federal Reserve economic calendar"],
                             "groundingChunks":[{"web":{"uri":"https://www.federalreserve.gov/",
                                                          "title":"Federal Reserve"}}] if citations else [],
                             "groundingSupports":[{"groundingChunkIndices":[0],
                                                   "segment":{"startIndex":0,"endIndex":8}}] if citations else []
                           }}]}

class FakeResponse:
    def __init__(self,x):self.x=x
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,max_bytes):return json.dumps(self.x).encode()

def setup(monkeypatch, payload):
    monkeypatch.setenv("GEMINI_API_KEY","test-only-api-key")
    monkeypatch.setenv("GEMINI_MODEL","gemini-3.5-flash-lite")
    def urlopen(req,timeout):
        sent=json.loads(req.data)
        assert sent["tools"]==[{"google_search":{}}]
        assert "responseMimeType" not in sent["generationConfig"]
        return FakeResponse(payload)
    monkeypatch.setattr("urllib.request.urlopen",urlopen)

def test_grounded_macro_and_technical_compatible(monkeypatch):
    setup(monkeypatch,response())
    out=compile_grounded("XAUUSD.h",[frame(x) for x in ("M1","M5","M15","H1")],"BUY",now=NOW)
    assert out["ai_confirmed"] is True
    assert out["fundamental_status"]=="GROUNDED_CLEAR"
    assert out["sources"][0]["title"]=="Federal Reserve"

def test_news_nearby_blocks_even_when_ai_direction_agrees(monkeypatch):
    setup(monkeypatch,response(last_minutes=12,next_minutes=200))
    out=compile_grounded("XAUUSD.h",[frame(x) for x in ("M1","M5","M15","H1")],"BUY",now=NOW)
    assert out["ai_confirmed"] is False
    assert out["fundamental_status"]=="NEWS_BLACKOUT"

def test_missing_grounding_sources_blocks(monkeypatch):
    setup(monkeypatch,response(citations=False))
    with pytest.raises(GroundingUnavailable):
        compile_grounded("XAUUSD.h",[frame(x) for x in ("M1","M5","M15","H1")],"BUY",now=NOW)

def test_conflicting_macro_blocks(monkeypatch):
    setup(monkeypatch,response(bias="SELL"))
    out=compile_grounded("XAUUSD.h",[frame(x) for x in ("M1","M5","M15","H1")],"BUY",now=NOW)
    assert out["ai_confirmed"] is False

def test_no_key_fails_closed(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY",raising=False)
    with pytest.raises(GroundingUnavailable):
        compile_grounded("XAUUSD.h",[frame(x) for x in ("M1","M5","M15","H1")],"BUY",now=NOW)

def test_missing_release_timestamp_fails_closed(monkeypatch):
    p=response()
    p["candidates"][0]["content"]["parts"][0]["text"] = json.dumps({
        "decision":"BUY","macro_bias":"BUY",
        "last_high_impact_utc":None,
        "next_high_impact_utc":(NOW+timedelta(minutes=150)).isoformat()})
    setup(monkeypatch,p)
    with pytest.raises(GroundingUnavailable):
        compile_grounded("XAUUSD.h",[frame(x) for x in ("M1","M5","M15","H1")],"BUY",now=NOW)
