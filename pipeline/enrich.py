"""
Step 3 - Enrich cleaned POIs with facts the place-search API cannot give us.

Why this step exists
--------------------
After clean.py, the signal count per POI is very uneven:

    food / cafe   rich  - dish tags, ratings, price, photos
    park / culture / weird  poor  - often just a name and coordinates

Since generate.py refuses to write copy from fewer than MIN_SIGNALS verified
facts, the poor categories were being skipped - which would quietly break
category rotation, the one mechanic the product depends on. This step closes
that gap with three sources chosen to hit exactly those weak spots:

    wikipedia  -> 建成年份 / 文保身份     (parks, museums, temples, old venues)
    regeo      -> 周边热闹或安静 / 所属园区 (everything with coordinates)
    qweather   -> 月相                    (everything; a date-level fact)

Design rules
------------
* Every source is optional. Missing credentials or a dead endpoint degrades to
  "one fewer signal", never to a crash.
* Everything is cached on disk by POI id, so a rerun costs no quota and an
  interrupted run resumes.
* Only categories that need a source pay for it - we do not spend Wikipedia
  lookups on the 800th noodle shop.

Usage:
    export AMAP_KEY=...                 # for regeo
    export QWEATHER_API_KEY=...         # or QWEATHER_KEY_ID/PROJECT_ID/PRIVATE_KEY
    python3 pipeline/enrich.py [--limit N] [--workers 4] [--skip-wiki]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import CLEAN_POI_FILE, ENRICH_CACHE_FILE  # noqa: E402
from pipeline.sources import amap_regeo, qweather, wikipedia  # noqa: E402

# Wikipedia only pays off where places are notable enough to have an article.
# Running it over every restaurant wastes hours and matches almost nothing.
WIKI_CATEGORIES = {"park", "culture", "weird", "night"}

# Names that hint at a place with actual history, regardless of category.
HISTORIC_HINTS = ("公园", "遗址", "故居", "寺", "庙", "塔", "园", "馆",
                  "书店", "剧场", "剧院", "教堂", "胡同", "旧址")

_cache_lock = threading.Lock()
_print_lock = threading.Lock()


def load_cache() -> dict:
    path = Path(ENRICH_CACHE_FILE)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    path = Path(ENRICH_CACHE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def wants_wiki(poi: dict) -> bool:
    """Decide whether a Wikipedia lookup is worth the request."""
    if poi.get("category") in WIKI_CATEGORIES:
        return True
    name = poi.get("name") or ""
    return any(hint in name for hint in HISTORIC_HINTS)


def enrich_one(poi: dict, cache: dict, use_wiki: bool, use_regeo: bool,
               amap_key: str | None) -> dict:
    """
    Fetch every applicable extra fact for one POI.

    Returns the extras dict (also written into the cache). Failures of any
    single source are absorbed - we always return whatever did succeed.
    """
    poi_id = poi.get("id") or poi.get("name")
    with _cache_lock:
        cached = cache.get(poi_id)
    if cached is not None:
        return cached

    extras: dict = {}

    if use_regeo and poi.get("lng") is not None:
        try:
            around = amap_regeo.surroundings(poi["lng"], poi["lat"], amap_key)
        except Exception:            # never let one POI kill the batch
            around = None
        if around:
            extras["poi_density"] = around["poi_density"]
            extras["lively_count"] = around["lively_count"]
            if around.get("aoi_name"):
                extras["aoi_name"] = around["aoi_name"]
            if around.get("township"):
                extras["township"] = around["township"]

    if use_wiki and wants_wiki(poi):
        try:
            found = wikipedia.lookup(poi.get("name", ""))
        except Exception:
            found = None
        if found:
            extras.update(found)

    with _cache_lock:
        cache[poi_id] = extras
    return extras


def fetch_moon_calendar(days: int = 7) -> dict:
    """
    Moon phase for the next few days, fetched once for the whole district.

    Moon phase is the same for every POI in 朝阳区, so this is one request
    rather than a thousand. Copy generated today can legitimately reference
    tonight's phase.
    """
    if not qweather.is_configured():
        print("  (QWeather not configured - skipping moon/sun signals)")
        return {}

    calendar: dict = {}
    today = datetime.now().strftime("%Y%m%d")
    moon = qweather.moon_phase(today)
    if moon:
        calendar["moon"] = moon
        print(f"  moon today: {moon.get('phase')} "
              f"(illumination {moon.get('illumination')})")
    sun = qweather.sun_times(today)
    if sun:
        calendar["sun"] = sun
        print(f"  sunset today: {sun.get('sunset')}")
    forecast = qweather.daily_forecast(days=min(days, 3))
    if forecast:
        calendar["forecast"] = forecast
    return calendar


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich POIs with extra sources")
    parser.add_argument("--limit", type=int, default=0,
                        help="0 = all")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-wiki", action="store_true")
    parser.add_argument("--skip-regeo", action="store_true")
    args = parser.parse_args()

    clean_path = Path(CLEAN_POI_FILE)
    if not clean_path.exists():
        print(f"ERROR: {clean_path} not found. Run collect.py + clean.py first.",
              file=sys.stderr)
        return 1

    pois = json.loads(clean_path.read_text(encoding="utf-8"))
    if args.limit:
        pois = pois[: args.limit]

    amap_key = os.environ.get("AMAP_KEY", "").strip()
    use_regeo = bool(amap_key) and not args.skip_regeo
    if not use_regeo and not args.skip_regeo:
        print("  (AMAP_KEY not set - skipping surroundings signals)")
    use_wiki = not args.skip_wiki

    print("Fetching district-level astronomy ...")
    calendar = fetch_moon_calendar()

    print(f"Enriching {len(pois)} POIs "
          f"(wiki={use_wiki}, regeo={use_regeo}, {args.workers} workers)")

    cache = load_cache()
    stats = {"wiki": 0, "inception": 0, "heritage": 0, "dynasty": 0,
             "regeo": 0, "aoi": 0}
    done = 0

    # Wikipedia self-throttles to one request per 200ms, so extra workers only
    # help the regeo calls. 4 is a good balance against AMap's 3 QPS cap.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(enrich_one, poi, cache, use_wiki, use_regeo, amap_key): poi
            for poi in pois
        }
        for future in as_completed(futures):
            done += 1
            extras = future.result() or {}
            if extras.get("wiki_title"):
                stats["wiki"] += 1
            if extras.get("inception"):
                stats["inception"] += 1
            if extras.get("is_heritage"):
                stats["heritage"] += 1
            if extras.get("dynasty"):
                stats["dynasty"] += 1
            if extras.get("poi_density") is not None:
                stats["regeo"] += 1
            if extras.get("aoi_name"):
                stats["aoi"] += 1
            if done % 50 == 0:
                with _cache_lock:
                    save_cache(cache)
                with _print_lock:
                    print(f"  progress {done}/{len(pois)}  {stats}")

    save_cache(cache)

    # Merge the extras back into clean_poi.json so generate.py needs no changes
    # beyond reading the new fields.
    merged = []
    for poi in pois:
        extras = cache.get(poi.get("id") or poi.get("name")) or {}
        record = {**poi, **extras}
        if calendar.get("moon"):
            record["moon_phase"] = calendar["moon"].get("phase")
        if calendar.get("sun"):
            record["sunset"] = calendar["sun"].get("sunset")
        merged.append(record)

    clean_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    print(f"\nEnriched {len(merged)} POIs")
    print(f"  wikipedia matched : {stats['wiki']}")
    print(f"  inception year    : {stats['inception']}")
    print(f"  heritage listed   : {stats['heritage']}")
    print(f"  dynasty only      : {stats['dynasty']}")
    print(f"  surroundings      : {stats['regeo']}")
    print(f"  named AOI         : {stats['aoi']}")
    print(f"Updated -> {clean_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
