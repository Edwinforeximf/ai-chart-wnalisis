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

def frame(tf, symbol="XAUUSD", bullish=True):
    return {"symbol":symbol, "timeframe":tf,
            "candles":[{"time":1770000000 + 3600*i, "open":2700,
                        "close":2708,"high":2710,"low":2695} for i in range(40)],
            "indicators":{"ema20":2703 if bullish else 2709,
                          "ema50":2700 if bullish else 2712,
                          "macd_main":1 if bullish else -1,
                          "macd_signal":0,"adx14":29,"atr14":12,
                          "rsi14":62 if bullish else 39,
                          "di_plus":30 if bullish else 15,
                          "di_minus":14 if bullish else 30}}

def publish(login, frames=None):
    payload={"platform":"MT4", "login":login, "server":"Broker-Demo",
             "broker":"Demo","currency":"USD","balance":1000,"equity":1000,
             "margin":0,"free_margin":1000,"positions":[],"history":[],
             "frames":frames if frames is not None else
                      [frame(tf) for tf in ["M1","M5","M15","H1","H4","D1"]]}
    r=client.post("/api/bridge/push",json=payload,headers=AUTH)
    assert r.status_code==200,r.text
    return {"platform":"MT4","login":login,"server":"Broker-Demo","symbol":"XAUUSD"}

def compiled_ok(*args, **kwargs):
    return {
        "ai_confirmed":True, "news_clear":True,
        "fundamental_status":"GROUNDED_CLEAR",
        "fundamental_summary":"Kondisi makro mendukung; sumber terverifikasi.",
        "reason":"EMA dan rilis ekonomi selaras.",
        "risk":"Perubahan sentimen mendadak.",
        "sources":[{"title":"Official data","url":"https://www.bls.gov/"}],
        "method":"GEMINI_GOOGLE_SEARCH_GROUNDING"
    }

def test_research_readonly_even_when_server_enabled(monkeypatch):
    p=publish(99871)
    monkeypatch.setenv("DEMO_EA_STRATEGY_ENABLED","true")
    monkeypatch.setattr(strategy_bridge,"compile_grounded",compiled_ok)
    assert client.post("/api/live/ai-compiled",json=p).status_code==401
    r=client.post("/api/live/ai-compiled",json=p,headers=DASH)
    assert r.status_code==200,r.text
    data=r.json()
    assert data["bias"]=="BUY"
    assert data["fundamental_status"]=="GROUNDED_CLEAR"
    assert data["trade_eligible"] is False
    assert data["sources"]
    assert client.get("/api/live/status",headers=DASH).json()["broker_execution_enabled"] is False

def test_disabled_default_blocks_ea_even_if_ai_would_agree(monkeypatch):
    p=publish(99872)
    monkeypatch.delenv("DEMO_EA_STRATEGY_ENABLED",raising=False)
    monkeypatch.setattr(strategy_bridge,"compile_grounded",compiled_ok)
    r=client.post("/api/bridge/strategy",json=p,headers=AUTH)
    assert r.status_code==200
    assert r.text.startswith("BLOCK|")
    assert client.post("/api/bridge/strategy",json=p).status_code==401

def test_enabled_demo_signal_requires_grounded_ai(monkeypatch):
    p=publish(99873)
    monkeypatch.setenv("DEMO_EA_STRATEGY_ENABLED","true")
    monkeypatch.setattr(strategy_bridge,"compile_grounded",compiled_ok)
    r=client.post("/api/bridge/strategy",json=p,headers=AUTH)
    assert r.text.startswith("ALLOW|BUY|")
    assert int(r.text.split("|")[2])>=51

def test_uncited_fundamentals_fail_closed(monkeypatch):
    from ai_macro_grounding import GroundingUnavailable
    p=publish(99874)
    monkeypatch.setenv("DEMO_EA_STRATEGY_ENABLED","true")
    monkeypatch.setattr(strategy_bridge,"compile_grounded",
                        lambda *args: (_ for _ in ()).throw(GroundingUnavailable("No citations")))
    r=client.post("/api/bridge/strategy",json=p,headers=AUTH)
    assert r.text.startswith("BLOCK|")
    plan=client.post("/api/live/ai-compiled",json=p,headers=DASH).json()
    assert plan["fundamental_status"]=="UNVERIFIED"
    assert not plan["trade_eligible"]

def test_technical_mismatch_blocks_before_ai(monkeypatch):
    p=publish(99875,frames=[frame("M15"),frame("H1")])
    monkeypatch.setenv("DEMO_EA_STRATEGY_ENABLED","true")
    monkeypatch.setattr(strategy_bridge,"compile_grounded",
                        lambda *args: (_ for _ in ()).throw(AssertionError("Should not search")))
    r=client.post("/api/bridge/strategy",json=p,headers=AUTH)
    assert r.text.startswith("BLOCK|")

def test_unmatched_account_and_stale_feed_block():
    p=publish(99876)
    p["server"]="Wrong-Server"
    assert client.post("/api/bridge/strategy",json=p,headers=AUTH).text=="BLOCK|NONE|0|0"
    p["server"]="Broker-Demo"
    key=strategy_bridge.account_key(strategy_bridge.StrategyRequest(**p))
    with live_bridge._lock:
        live_bridge._snapshots[key]["received_at"]=time.time()-300
    assert client.post("/api/bridge/strategy",json=p,headers=AUTH).text=="BLOCK|NONE|0|0"

def test_demo_ea_sources_have_strict_controls():
    root=Path(__file__).resolve().parents[1]/"bridges"
    for name in ["MT4_AutoSwitch_DEMO.mq4","MT5_AutoSwitch_DEMO.mq5",
                 "MT4_Scalper_Fundamental_DEMO.mq4","MT5_Scalper_Fundamental_DEMO.mq5"]:
        code=(root/name).read_text()
        assert "EnableDemoOrders=false" in code or "EnableDemoOrders = false" in code
        assert "ACCOUNT_TRADE_MODE_DEMO" in code
        assert "MaxRiskPercent" in code
        assert "ReverseCooldownSeconds" in code


def test_demo_check_does_not_shadow_meta_trader_builtin():
    root=Path(__file__).resolve().parents[1]/"bridges"
    for filename in ["MT4_AutoSwitch_DEMO.mq4","MT5_AutoSwitch_DEMO.mq5",
                     "MT4_Scalper_Fundamental_DEMO.mq4","MT5_Scalper_Fundamental_DEMO.mq5"]:
        source=(root/filename).read_text(encoding="utf-8")
        assert "IsDemo(" not in source and "IsDemo (" not in source
        assert "bool IsDemoAccountAI()" in source
        assert "ACCOUNT_TRADE_MODE_DEMO" in source
