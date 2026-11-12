import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["BRIDGE_TOKEN"] = "b" * 32
os.environ["DASHBOARD_TOKEN"] = "d" * 32

from fastapi.testclient import TestClient
from app import app

client = TestClient(app)
BRIDGE = {"X-Bridge-Key": "b" * 32}
DASH = {"Authorization": "Bearer " + "d" * 32}

def sample(symbol="XAUUSD", login=999888, frames=("M15", "H1", "H4", "D1")):
    rows = [
        {"time": 1790000000 + i * 60, "open": 2700 + i * .1,
         "high": 2701 + i * .1, "low": 2699 + i * .1, "close": 2700.4 + i * .1}
        for i in range(40)
    ]
    return {
        "platform": "MT5", "login": login, "server": "Broker-Demo", "broker": "Test",
        "currency": "USD", "balance": 10000, "equity": 10021,
        "margin": 100, "free_margin": 9921,
        "positions": [{"ticket": 1, "symbol": symbol, "side": "BUY",
                       "volume": .1, "profit": 21}],
        "history": [],
        "frames": [{"symbol": symbol, "timeframe": tf, "candles": rows,
                    "indicators": {"ema20": 2701, "rsi14": 55, "atr14": 3.3}}
                   for tf in frames]
    }

def test_authenticated_bridge_snapshot():
    assert client.post("/api/bridge/push", json=sample()).status_code == 401
    assert client.get("/api/live/accounts").status_code == 401
    r = client.post("/api/bridge/push", json=sample(), headers=BRIDGE)
    assert r.status_code == 200, r.text
    key = r.json()["account_key"]
    a = client.get("/api/live/accounts", headers=DASH)
    assert a.status_code == 200
    assert any(x["account_key"] == key for x in a.json()["accounts"])
    s = client.get(f"/api/live/snapshot/{key}", headers=DASH)
    assert s.status_code == 200
    assert len(s.json()["frames"]) == 4
    assert s.json()["positions"][0]["ticket"] == 1

def test_multi_ea_symbol_merging():
    first = client.post("/api/bridge/push", json=sample("XAUUSD", 123), headers=BRIDGE)
    key = first.json()["account_key"]
    second = client.post("/api/bridge/push", json=sample("EURUSD", 123, frames=("H1", "H4")), headers=BRIDGE)
    assert second.status_code == 200
    snapshot = client.get(f"/api/live/snapshot/{key}", headers=DASH).json()
    assert {"XAUUSD", "EURUSD"} == {x["symbol"] for x in snapshot["frames"]}

def test_no_trading_and_ai_fails_closed(monkeypatch):
    r = client.post("/api/bridge/push", json=sample(login=555), headers=BRIDGE)
    key = r.json()["account_key"]
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = client.post("/api/live/analyze", json={"account_key": key, "symbol": "XAUUSD"}, headers=DASH)
    assert r.status_code == 503
    assert client.get("/api/live/status", headers=DASH).json()["broker_execution_enabled"] is False
    assert client.get("/live").status_code == 200
