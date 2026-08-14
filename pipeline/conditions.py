"""
Emit the current conditions as JSON, for the app's /api/conditions endpoint.

This is the run-time counterpart to enrich.py: enrich.py bakes *place* facts
into gacha.json at build time, while this reports what is true *right now*.
Keeping it here (rather than in the frontend) means the QWeather credential
stays server-side, where it belongs.

Always exits 0 and always prints valid JSON - an empty object when nothing is
available. The client treats weather as a bonus, never a dependency.

Usage:
    python3 pipeline/conditions.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.sources import qweather  # noqa: E402


def collect() -> dict:
    if not qweather.is_configured():
        return {}

    payload: dict = {}
    today = datetime.now().strftime("%Y%m%d")

    now = qweather.current_weather()
    if now and now.get("text"):
        payload["weather"] = {
            "text": now["text"],
            "temp": now.get("temp"),
            "feelsLike": now.get("feels_like"),
            "visibility": now.get("visibility"),
        }

    moon = qweather.moon_phase(today)
    if moon and moon.get("phase"):
        payload["moonPhase"] = moon["phase"]

    sun = qweather.sun_times(today)
    if sun and sun.get("sunset"):
        payload["sunset"] = sun["sunset"]

    return payload


def main() -> int:
    try:
        payload = collect()
    except Exception:
        # This process feeds a web request; a stack trace on stdout would
        # corrupt the response. Degrade to "no conditions known".
        payload = {}
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
