import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fundamental_gate

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

class FakeResponse:
    def __init__(self, content):
        self.body = json.dumps(content).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, n):
        return self.body[:n]

def event(offset, name="US CPI", importance=3):
    return {
        "CalendarId": str(1000 + offset),
        "Date": (NOW + timedelta(minutes=offset)).replace(tzinfo=None).isoformat(),
        "Country": "United States",
        "Event": name,
        "Category": "Inflation",
        "Actual": "",
        "Forecast": "2.7%",
        "DateSpan": "0",
        "Importance": importance,
    }

def stub_calendar(monkeypatch, events):
    monkeypatch.setenv("TRADING_ECONOMICS_API_KEY", "test-only-calendar-api-key")
    with fundamental_gate._lock:
        fundamental_gate._cache.clear()
    called = []
    def urlopen(req, timeout):
        called.append(req.full_url)
        return FakeResponse(events)
    monkeypatch.setattr(fundamental_gate.urllib.request, "urlopen", urlopen)
    return called

def test_missing_provider_fails_closed(monkeypatch):
    monkeypatch.delenv("TRADING_ECONOMICS_API_KEY", raising=False)
    with fundamental_gate._lock:
        fundamental_gate._cache.clear()
    with pytest.raises(fundamental_gate.CalendarUnavailable):
        fundamental_gate.calendar_context("XAUUSD.h", now=NOW)

def test_high_impact_news_blackout(monkeypatch):
    stub_calendar(monkeypatch, [event(25), event(160)])
    result = fundamental_gate.calendar_context("XAUUSD.h", now=NOW)
    assert result["provider_confirmed"]
    assert result["source"] == "TRADING_ECONOMICS_CALENDAR"
    assert result["status"] == "NEWS_BLACKOUT"
    assert not result["can_open_new_position"]
    assert result["news_blackout_events"][0]["event"] == "US CPI"

def test_calendar_clear_not_directional_confirmation(monkeypatch):
    stub_calendar(monkeypatch, [event(200)])
    result = fundamental_gate.calendar_context("XAUUSD.h", now=NOW)
    assert result["status"] == "CALENDAR_CLEAR"
    assert result["can_open_new_position"]
    assert result["directional_macro_bias"] == "NOT_VERIFIED"

def test_calendar_response_is_cached(monkeypatch):
    calls=stub_calendar(monkeypatch, [event(200)])
    fundamental_gate.calendar_context("XAUUSD.h", now=NOW)
    fundamental_gate.calendar_context("XAUUSD.h", now=NOW+timedelta(seconds=60))
    assert len(calls)==1
    assert "calendar/country/united%20states" in calls[0]
    assert "importance=3" in calls[0]

def test_empty_provider_is_not_clear(monkeypatch):
    stub_calendar(monkeypatch, [])
    with pytest.raises(fundamental_gate.CalendarUnavailable):
        fundamental_gate.calendar_context("EURUSD", now=NOW)

def test_estimated_news_blocks_until_precision_known(monkeypatch):
    e=event(550)
    e["DateSpan"]="1"
    stub_calendar(monkeypatch, [e])
    result=fundamental_gate.calendar_context("XAUUSD.h", now=NOW)
    assert result["status"]=="NEWS_BLACKOUT"
    assert result["news_blackout_events"][0]["estimated"]

def test_symbol_country_mapping():
    assert fundamental_gate._countries("XAUUSD.h")==("united states",)
    assert set(fundamental_gate._countries("EURUSD"))=={"euro area","united states"}
    assert set(fundamental_gate._countries("USDJPY"))=={"united states","japan"}
    with pytest.raises(fundamental_gate.CalendarUnavailable):
        fundamental_gate._countries("XYZQQQ")
