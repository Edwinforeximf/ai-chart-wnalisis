import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["BRIDGE_TOKEN"] = "b" * 32
os.environ["DASHBOARD_TOKEN"] = "d" * 32
os.environ["DASHBOARD_PIN"] = "4826"

from fastapi.testclient import TestClient
from app import app
from live_bridge import _indicator_alignment

def test_pin_auth_cookie_and_logout():
    with TestClient(app, base_url="https://testserver") as browser:
        r = browser.get("/api/live/status")
        assert r.status_code == 401
        assert browser.post("/api/live/login", json={"pin": "0000"}).status_code == 401
        assert browser.post("/api/live/login", json={"pin": "4826"}).status_code == 200
        cookie = browser.cookies.get("tt_session")
        assert cookie and len(cookie) >= 32
        assert browser.get("/api/live/session").json()["authenticated"] is True
        assert browser.get("/api/live/status").status_code == 200
        assert browser.post("/api/live/logout").status_code == 200
        assert browser.get("/api/live/status").status_code == 401

def test_pin_limited_after_five_failures():
    from live_bridge import _pin_attempts, _global_pin_failures, _lock
    with _lock:
        _pin_attempts.clear()
        _global_pin_failures.clear()
    with TestClient(app, base_url="https://testserver") as browser:
        for _ in range(5):
            assert browser.post("/api/live/login", json={"pin": "9999"}).status_code == 401
        assert browser.post("/api/live/login", json={"pin": "4826"}).status_code == 429
    with _lock:
        _pin_attempts.clear()
        _global_pin_failures.clear()

def test_alignment_score_is_heuristic():
    frames = [{"timeframe": "H1", "candles": [{"close": 2705}],
               "indicators": {"ema20": 2700, "ema50": 2690,
                              "macd_main": 4, "macd_signal": 2,
                              "di_plus": 28, "di_minus": 12, "rsi14": 66}},
              {"timeframe": "H4", "candles": [{"close": 2707}],
               "indicators": {"ema20": 2701, "ema50": 2695,
                              "macd_main": 5, "macd_signal": 1,
                              "di_plus": 30, "di_minus": 12, "rsi14": 62}}]
    result = _indicator_alignment(frames)
    assert result["bias"] == "BUY"
    assert result["confidence_pct"] > 60
    assert result["confidence_pct"] <= 90
    assert result["score_type"] == "INDICATOR_ALIGNMENT_NOT_WIN_PROBABILITY"

def test_short_ai_response_without_wait(monkeypatch):
    from live_bridge import _snapshots, _lock
    import time
    import types
    example = {"time": 1790000000, "open": 2700, "high": 2701,
               "low": 2698, "close": 2700.5}
    def frame(tf):
        return {"symbol": "XAUUSD", "timeframe": tf,
                "candles": [example] * 40,
                "indicators": {"ema20": 2699, "ema50": 2690,
                               "macd_main": 2, "macd_signal": 1,
                               "rsi14": 63}}
    now=time.time()
    with _lock:
        _snapshots["test-pin-ai"] = {
            "received_at":now, "snapshot":{"platform":"MT4","login":123,"server":"test",
                "broker":"Test","currency":"USD","balance":1000,"equity":1001,
                "margin":0,"free_margin":1001,"positions":[],"history":[]},
            "frame_map":{("XAUUSD",tf):{"received_at":now,"frame":frame(tf)}
                        for tf in ["H1","H4"]}
        }
    monkeypatch.setenv("GEMINI_API_KEY","test-gemini-key")
    class FakeReply:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,*args):return json.dumps({"candidates":[{"content":{"parts":[{"text":json.dumps({"reason":"EMA menguat pada H1 dan H4.","risk":"Fundamental belum diverifikasi."})}]}}]}).encode()
    monkeypatch.setattr("urllib.request.urlopen",lambda *args,**kwargs:FakeReply())
    with TestClient(app,base_url="https://testserver") as browser:
        r=browser.post("/api/live/analyze",headers={"Authorization":"Bearer "+"d"*32},
                       json={"account_key":"test-pin-ai","symbol":"XAUUSD"})
        assert r.status_code==200, r.text
        result=r.json()
        assert result["bias"] in ("BUY","SELL")
        assert isinstance(result["confidence_pct"],int)
        assert result["fundamental_status"]=="NOT_CONNECTED"
        assert result["broker_order_placed"] is False
        assert "reason" in result and "risk" in result
        assert "analysis" not in result
