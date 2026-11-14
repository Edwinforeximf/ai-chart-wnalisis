import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["BRIDGE_TOKEN"] = "b" * 32
os.environ["DASHBOARD_TOKEN"] = "d" * 32

from fastapi.testclient import TestClient
from app import app
import live_bridge
import strategy_bridge

client = TestClient(app)
AUTH = {"X-Bridge-Key": "b" * 32}
DASH = {"Authorization": "Bearer " + "d" * 32}

def make_frame(tf, symbol="XAUUSD", bullish=True):
    return {
        "symbol": symbol,
        "timeframe": tf,
        "candles": [
            {"time": 1770000000 + 3600*i, "open": 2700.0, "close": 2708.0,
             "high": 2710.0, "low": 2695.0}
            for i in range(40)
        ],
        "indicators": {
            "ema20": 2703.0 if bullish else 2709.0,
            "ema50": 2700.0 if bullish else 2712.0,
            "macd_main": 1.0 if bullish else -1.0,
            "macd_signal": 0.0,
            "adx14": 29.0,
            "atr14": 12.0,
            "rsi14": 62.0 if bullish else 39.0,
            "di_plus": 30.0 if bullish else 15.0,
            "di_minus": 14.0 if bullish else 30.0,
        }
    }

def publish(server="Broker-Demo",login=99881,frames=None):
    payload = {
        "platform":"MT4", "login":login, "server":server, "broker":"Demo",
        "currency":"USD","balance":1000,"equity":1000,
        "margin":0,"free_margin":1000,"positions":[],"history":[],
        "frames":frames or [make_frame(tf) for tf in ["M15","H1","H4","D1"]],
    }
    r=client.post("/api/bridge/push",json=payload,headers=AUTH)
    assert r.status_code == 200,r.text
    return {"platform":"MT4","login":login,"server":server,"symbol":"XAUUSD"}

def test_no_broker_orders_and_no_auth_bypass(monkeypatch):
    monkeypatch.delenv("DEMO_EA_STRATEGY_ENABLED", raising=False)
    p=publish(login=99871)
    assert client.post("/api/bridge/strategy",json=p).status_code == 401
    r=client.post("/api/live/strategy-preview",json=p,headers=DASH)
    assert r.status_code == 200
    j=r.json()
    assert j["confidence_pct"]>=51
    assert j["score_type"]=="INDICATOR_ALIGNMENT_NOT_WIN_PROBABILITY"
    assert j["trade_eligible"] is False
    assert j["server_demo_enabled"] is False
    assert j["mode"]=="DEMO_ACCOUNT_ONLY"

def test_ai_agreement_unlocks_only_demo_signal(monkeypatch):
    monkeypatch.setenv("DEMO_EA_STRATEGY_ENABLED", "true")
    p=publish(login=99872)
    monkeypatch.setattr(strategy_bridge,"_model_check",lambda plan,frames:(True,"EMA & MACD aligned"))
    r=client.post("/api/bridge/strategy",json=p,headers=AUTH)
    assert r.status_code == 200
    parts=r.text.split("|")
    assert parts[0]=="ALLOW"
    assert parts[1]=="BUY"
    assert int(parts[2])>=51
    assert int(parts[3])>0
    assert client.get("/api/live/status",headers=DASH).json()["broker_execution_enabled"] is False

def test_technical_risk_veto(monkeypatch):
    monkeypatch.setenv("DEMO_EA_STRATEGY_ENABLED", "true")
    p=publish(login=99873,frames=[make_frame(tf) for tf in ["M15","H1"]])
    monkeypatch.setattr(strategy_bridge,"_model_check",lambda plan,frames:(True,"AI agreed"))
    r=client.post("/api/bridge/strategy",json=p,headers=AUTH)
    assert r.text.startswith("BLOCK|")

def test_disagreement_veto(monkeypatch):
    monkeypatch.setenv("DEMO_EA_STRATEGY_ENABLED", "true")
    p=publish(login=99874)
    monkeypatch.setattr(strategy_bridge,"_model_check",lambda plan,frames:(False,"Conflicting indicators"))
    r=client.post("/api/bridge/strategy",json=p,headers=AUTH)
    assert r.text.startswith("BLOCK|")

def test_account_mismatch_and_missing_data_fail_closed():
    p=publish(login=99875)
    p["server"]="Unexpected"
    assert client.post("/api/bridge/strategy",json=p,headers=AUTH).text == "BLOCK|NONE|0|0"
    p["login"]=999999
    assert client.post("/api/bridge/strategy",json=p,headers=AUTH).text == "BLOCK|NONE|0|0"

def test_stale_feed_blocks(monkeypatch):
    p=publish(login=99876)
    key=strategy_bridge.account_key(strategy_bridge.StrategyRequest(**p))
    with live_bridge._lock:
        live_bridge._snapshots[key]["received_at"]=time.time()-300
    assert client.post("/api/bridge/strategy",json=p,headers=AUTH).text=="BLOCK|NONE|0|0"

def test_ea_sources_demo_guarded():
    root=Path(__file__).resolve().parents[1]/"bridges"
    for file in ["MT4_AutoSwitch_DEMO.mq4","MT5_AutoSwitch_DEMO.mq5"]:
        s=(root/file).read_text(encoding="utf-8")
        assert "EnableDemoOrders=false" in s or "EnableDemoOrders = false" in s
        assert "ACCOUNT_TRADE_MODE_DEMO" in s
        assert "MaxRiskPercent" in s and "MaxDailyDrawdownPercent" in s
        assert "ReverseCooldownSeconds" in s
        assert "OrderClose" in s or "PositionClose" in s
