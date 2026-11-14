"""Grounded Gemini research: native MT indicators + latest macro web evidence.

No third-party economic calendar API. Search results are not complete calendar
coverage. This demo strategy fails closed if sources or event times are unknown.
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

class GroundingUnavailable(Exception):
    pass

def _timestamp(v):
    if not isinstance(v, str) or not v:
        raise GroundingUnavailable("High impact event timestamp missing")
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if t.tzinfo is None:
            raise ValueError("Timezone required")
        return t.astimezone(timezone.utc).timestamp()
    except (ValueError, OverflowError):
        raise GroundingUnavailable("Unverified event timestamp") from None

def _parse_text(text):
    if not isinstance(text, str):
        raise GroundingUnavailable("Invalid AI answer")
    t = text.strip()
    fence = chr(96) * 3
    if t.startswith(fence):
        t = t.split("\n", 1)[-1].rsplit(fence, 1)[0].strip()
    try:
        obj = json.loads(t)
        if not isinstance(obj, dict):
            raise ValueError("not object")
        return obj
    except ValueError:
        raise GroundingUnavailable("Gemini did not return JSON object") from None

def compile_grounded(symbol, frames, proposed_bias, *, now=None):
    now = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    key = os.getenv("GEMINI_API_KEY", "")
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    if not key or not re.fullmatch(r"[A-Za-z0-9._-]{3,72}", model):
        raise GroundingUnavailable("Gemini key/model not configured")
    if proposed_bias not in ("BUY", "SELL") or len(frames) < 4:
        raise GroundingUnavailable("Insufficient native indicator data")
    prompt = (
        "Anda analis teknikal+fundamental scalping. Pakai GOOGLE SEARCH "
        "untuk memeriksa berita terkini dan jadwal ekonomi dari sumber "
        "dapat diverifikasi. Analisa dampak USD, yield, bank sentral, "
        "FOMC, CPI, NFP, dan berita relevan dengan simbol. "
        "Cari rilis high-impact terbaru dan jadwal rilis high-impact "
        "berikutnya, lengkap tanggal/jam UTC. Jika jam tidak terverifikasi "
        "isi null, jangan mengarang. Perhatikan Google Search bukan kalender "
        "lengkap. Bandingkan indikator native MetaTrader. Jangan mengarang "
        "harga berita atau persen peluang menang. "
        "Jawab HANYA JSON object fields: decision BUY/SELL/NO_TRADE; "
        "macro_bias BUY/SELL/NEUTRAL/UNKNOWN; reason maksimal 20 kata, "
        "risk maksimal 15 kata; fundamental_summary maksimal 25 kata; "
        "last_high_impact_utc timestamp ISO UTC atau null; "
        "next_high_impact_utc timestamp ISO UTC atau null. "
        "Jika konflik data, NO_TRADE. Tanpa markdown. "
        + json.dumps({
            "now_utc": now.isoformat(), "symbol": symbol,
            "technical_bias": proposed_bias,
            "native_frames": [{
                "tf": f["timeframe"], "closed_candle": f["candles"][-1],
                "indicators": f["indicators"]} for f in frames]
        }, ensure_ascii=False, separators=(",", ":"))
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 500}
    }).encode()
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=body, method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            api_result = json.loads(response.read(300000))
        candidate = api_result["candidates"][0]
        report = _parse_text(candidate["content"]["parts"][0]["text"])
        metadata = candidate.get("groundingMetadata") or {}
        queries = metadata.get("webSearchQueries") or []
        supports = metadata.get("groundingSupports") or []
        sources = []
        for chunk in metadata.get("groundingChunks") or []:
            if not isinstance(chunk, dict):
                continue
            source = chunk.get("web") or {}
            uri = source.get("uri", "")
            if isinstance(uri, str) and uri.startswith("https://") and len(uri) < 1500:
                sources.append({"title": str(source.get("title", "Source"))[:100], "url": uri})
        if not queries or not supports or not sources:
            raise GroundingUnavailable("Grounding metadata missing")
        direction = report.get("decision")
        macro = report.get("macro_bias")
        if direction not in ("BUY", "SELL", "NO_TRADE") or macro not in (
                "BUY", "SELL", "NEUTRAL", "UNKNOWN"):
            raise GroundingUnavailable("Invalid AI classification")
        last = _timestamp(report.get("last_high_impact_utc"))
        future = _timestamp(report.get("next_high_impact_utc"))
        if not last <= now.timestamp() < future:
            raise GroundingUnavailable("Incomplete verified release sequence")
        news_clear = (now.timestamp()-last)>45*60 and (future-now.timestamp())>60*60
        return {
            "decision": direction,
            "macro_bias": macro,
            "reason": " ".join(str(report.get("reason", "")).split())[:135],
            "risk": " ".join(str(report.get("risk", "")).split())[:110],
            "fundamental_summary": " ".join(str(report.get("fundamental_summary", "")).split())[:190],
            "fundamental_status": "GROUNDED_CLEAR" if news_clear else "NEWS_BLACKOUT",
            "news_clear": news_clear,
            "ai_confirmed": news_clear and direction == proposed_bias and macro == proposed_bias,
            "sources": sources[:5],
            "last_high_impact_utc": report.get("last_high_impact_utc"),
            "next_high_impact_utc": report.get("next_high_impact_utc"),
            "searched_at_utc": now.isoformat(),
            "method": "GEMINI_GOOGLE_SEARCH_GROUNDING",
            "limits": "Google Search is not authoritative complete economic-calendar coverage; DEMO only"
        }
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            ValueError, KeyError, IndexError, TypeError):
        raise GroundingUnavailable("Gemini grounded research unavailable") from None
