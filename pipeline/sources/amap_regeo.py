"""
AMap reverse-geocoding client - the *surroundings* half of our real-data story.

Why this source
---------------
`/v3/geocode/regeo` answers a question the place search cannot: **what is it
like around here?** For one coordinate it returns nearby POIs, roads and AOI
(area-of-interest) membership. From that we derive two honest signals:

  poi_density  -> 安静 vs 热闹     ("附近很安静" / "周围很热闹")
  aoi_name     -> 它在哪个园区/商圈里 ("在798艺术区里面")

This is especially valuable for 公园 / 小众地方, which have no dish tags and
often no rating - surroundings may be the only concrete thing we can say.

Note it is an *inference*, not a measurement: POI count is a proxy for
liveliness, so `signal_crowd` phrases it as an impression ("周围很热闹"), never
as a statistic. Being honest about the strength of a claim is part of not
lying to the user.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://restapi.amap.com/v3/geocode/regeo"

# 800m keeps the count meaningful at walking scale. A larger radius would make
# every POI in 朝阳区 look "热闹" and the signal would carry no information.
RADIUS_M = 800
REQUEST_INTERVAL = 0.35

# Categories that indicate an area is *alive* rather than merely built-up.
# Counting banks and car parks as "热闹" would be misleading.
LIVELY_TYPES = ("餐饮", "购物", "休闲", "娱乐", "咖啡", "酒吧", "风景")


def _request(params: dict, retries: int = 3) -> dict | None:
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    delay = 1.0
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(delay)
            delay *= 2
            continue
        if payload.get("status") != "1":
            info = payload.get("info", "")
            if "LIMIT" in info or "BUSY" in info:
                time.sleep(delay)
                delay *= 2
                continue
            return None
        return payload
    return None


def surroundings(lng: float, lat: float, api_key: str | None = None) -> dict | None:
    """
    Describe what is around one coordinate.

    Returns {"poi_density": int, "lively_count": int, "aoi_name": str|None,
             "township": str|None} or None.
    """
    api_key = api_key or os.environ.get("AMAP_KEY", "").strip()
    if not api_key or lng is None or lat is None:
        return None

    payload = _request({
        "key": api_key,
        "location": f"{lng:.6f},{lat:.6f}",
        "extensions": "all",
        "radius": RADIUS_M,
        "output": "JSON",
    })
    time.sleep(REQUEST_INTERVAL)
    if not payload:
        return None

    regeocode = payload.get("regeocode") or {}
    pois = regeocode.get("pois") or []
    aois = regeocode.get("aois") or []
    component = regeocode.get("addressComponent") or {}

    lively = 0
    for poi in pois:
        poi_type = poi.get("type") or ""
        if isinstance(poi_type, str) and any(t in poi_type for t in LIVELY_TYPES):
            lively += 1

    aoi_name = None
    if aois:
        # Smallest AOI is the most specific ("798艺术区" beats "朝阳区").
        def _area(aoi):
            try:
                return float(aoi.get("area") or 0)
            except (TypeError, ValueError):
                return 0.0

        smallest = min(aois, key=_area) if any(_area(a) for a in aois) else aois[0]
        name = smallest.get("name")
        if isinstance(name, str) and name.strip():
            aoi_name = name.strip()

    township = component.get("township")
    if not isinstance(township, str) or not township.strip():
        township = None

    return {
        "poi_density": len(pois),
        "lively_count": lively,
        "aoi_name": aoi_name,
        "township": township,
    }
