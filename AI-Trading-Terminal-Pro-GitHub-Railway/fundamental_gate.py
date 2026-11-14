"""Economic calendar gate for the DEMO scalping strategy.

Trading Economics calendar data is used only after successful authenticated fetch.
It is NOT a model of the direction of the gold, FX, or oil price.
No key, stale data, ambiguous timestamp, provider failure => deny new positions.
"""
from datetime import datetime, timedelta, timezone
from threading import RLock
import json
import os
import re
import time
import urllib.parse
import urllib.request

_lock = RLock()
_cache = {}
TTL_SECONDS = 300  # avoid repeated provider requests on each EA poll
MAX_BYTES = 1_000_000
HIGH_IMPACT = 3
NORMAL_BEFORE = 30
NORMAL_AFTER = 30
MAJOR_BEFORE = 60
MAJOR_AFTER = 45
MAJOR_EVENT = re.compile(
    r"(non.?farm|payroll|inflation|consumer price|core.?pce|"
    r"fed|fomc|interest rate|central bank|policy decision|"
    r"employment change|unemployment rate|press conference|"
    r"gross domestic product|retail sales)", re.IGNORECASE)

class CalendarUnavailable(Exception):
    pass

def _countries(symbol):
    sym = symbol.upper()
    if sym.startswith(("XAU", "XAG", "USOIL", "WTI", "BRENT", "XTI")):
        return ("united states",)
    base = sym[:3]
    quote = sym[3:6]
    map_country = {
        "USD": "united states", "EUR": "euro area",
        "GBP": "united kingdom", "JPY": "japan",
        "CAD": "canada", "AUD": "australia",
        "NZD": "new zealand", "CHF": "switzerland",
    }
    if base not in map_country or quote not in map_country:
        # No safe symbol to macro-currency mapping for a non-listed commodity/exotic.
        raise CalendarUnavailable("Unsupported symbol for fundamental filtering")
    return tuple(sorted({map_country[base], map_country[quote]}))

def _utc_date(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Unrecognized calendar date")
    v = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Trading Economics documents Date as UTC, even when offset is omitted.
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)

def _fetch_calendar(countries, *, now):
    key = os.getenv("TRADING_ECONOMICS_API_KEY", "")
    if len(key) < 8:
        raise CalendarUnavailable("TRADING_ECONOMICS_API_KEY missing")
    start = (now - timedelta(days=1)).date().isoformat()
    end = (now + timedelta(days=2)).date().isoformat()
    slug = urllib.parse.quote(",".join(countries), safe=",")
    query = urllib.parse.urlencode({"c": key, "importance": 3, "f": "json"})
    url = (f"https://api.tradingeconomics.com/calendar/country/"
           f"{slug}/{start}/{end}?{query}")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read(MAX_BYTES + 1)
    except Exception:
        raise CalendarUnavailable("Calendar provider unreachable") from None
    if len(body) > MAX_BYTES:
        raise CalendarUnavailable("Calendar response too large")
    try:
        data = json.loads(body)
        if not isinstance(data, list) or not data:
            raise ValueError("Missing calendar records")
        out = []
        for e in data:
            if not isinstance(e, dict):
                continue
            if int(e.get("Importance", 0)) != HIGH_IMPACT:
                continue
            if str(e.get("Country", "")).casefold() not in countries:
                continue
            dt = _utc_date(e.get("Date"))
            out.append({
                "id": str(e.get("CalendarId", ""))[:32],
                "date_utc": dt.isoformat(),
                "epoch": dt.timestamp(),
                "event": str(e.get("Event", ""))[:120],
                "country": str(e.get("Country", ""))[:60],
                "actual": str(e.get("Actual", ""))[:30],
                "forecast": str(e.get("Forecast", ""))[:30],
                "precision": str(e.get("DateSpan", "0")),
            })
        return out
    except (ValueError, TypeError, OverflowError, KeyError):
        raise CalendarUnavailable("Invalid calendar response") from None

def calendar_context(symbol, *, now=None):
    moment = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    countries = _countries(symbol)
    stamp = moment.timestamp()
    with _lock:
        item = _cache.get(countries)
        if item and stamp - item["received_at"] < TTL_SECONDS and stamp >= item["received_at"]:
            events = item["events"]
        else:
            events = None
    if events is None:
        events = _fetch_calendar(countries, now=moment)
        with _lock:
            _cache[countries] = {"received_at": stamp, "events": events}
    windows = []
    recent = []
    upcoming = []
    for e in events:
        delta_min = (e["epoch"]-stamp)/60
        major = bool(MAJOR_EVENT.search(e["event"]))
        before = MAJOR_BEFORE if major else NORMAL_BEFORE
        after = MAJOR_AFTER if major else NORMAL_AFTER
        # Source may report approximate / not timed date (DateSpan != 0).
        # Do not assume a precise safe intraday window for that event.
        estimated_day = e["precision"] != "0" and abs(delta_min) < 24*60
        if estimated_day or -after <= delta_min <= before:
            windows.append({"event":e["event"], "country":e["country"],
                            "minutes_to_release":round(delta_min),
                            "date_utc":e["date_utc"],
                            "estimated":estimated_day})
        if -240 <= delta_min < 0:
            recent.append({"event":e["event"],"country":e["country"],
                           "date_utc":e["date_utc"],
                           "actual":e["actual"],"forecast":e["forecast"]})
        elif 0 <= delta_min <= 24*60:
            upcoming.append({"event":e["event"],"country":e["country"],
                             "date_utc":e["date_utc"]})
    windows.sort(key=lambda x: abs(x["minutes_to_release"]))
    recent.sort(key=lambda x: x["date_utc"], reverse=True)
    upcoming.sort(key=lambda x: x["date_utc"])
    return {
        "source": "TRADING_ECONOMICS_CALENDAR",
        "provider_confirmed": True,
        "status": "NEWS_BLACKOUT" if windows else "CALENDAR_CLEAR",
        "can_open_new_position": not bool(windows),
        "news_blackout_events": windows[:8],
        "recent_high_impact": recent[:6],
        "upcoming_high_impact": upcoming[:6],
        "directional_macro_bias": "NOT_VERIFIED",
        "explanation": "Economic calendar risk gate only; absence of news is not bullish/bearish confirmation.",
        "countries": countries,
        "fetched_age_seconds": round(stamp - _cache[countries]["received_at"]),
    }
