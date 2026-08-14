"""
Step 1 - Collect POI skeletons from the AMap Web Service API.

Only objective facts are taken from AMap (name, coordinates, type, address,
opening hours). All subjective copy is generated later by us, so we never
reproduce anyone else's review text.

AMap caps every distinct query at 100 pages x 25 = 2500 records, so a plain
"whole district" query silently truncates. We slice 朝阳区 into a grid and
query each cell separately, which keeps every cell well under the cap.

Usage:
    export AMAP_KEY=your_key
    python3 pipeline/collect.py [--grid 4] [--per-cat 400]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import (  # noqa: E402
    CATEGORIES,
    DISTRICT_ADCODE,
    DISTRICT_BBOX,
    RAW_POI_FILE,
)
from pipeline.db import database as db  # noqa: E402

# v5 is required: v3 cannot return tag / photos / opentime_today at all,
# and those are the fields that make the copy grounded rather than invented.
API_URL = "https://restapi.amap.com/v5/place/polygon"
SHOW_FIELDS = "business,photos"
PAGE_SIZE = 25
# v5 caps paging at 200 records per distinct query (8 pages x 25).
MAX_PAGE = 8
# AMap's free personal tier allows 3 QPS; stay comfortably below it.
REQUEST_INTERVAL = 0.35


def _request(params: dict, retries: int = 4) -> dict:
    """GET with exponential backoff. Returns {} when the call is unrecoverable."""
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            # status "0" means AMap rejected the call (quota, bad key, ...).
            if payload.get("status") != "1":
                info = payload.get("info", "UNKNOWN")
                # Throttling is retryable; a bad key is not.
                if "LIMIT" in info or "BUSY" in info or "TIMEOUT" in info:
                    time.sleep(delay)
                    delay *= 2
                    continue
                print(f"  ! AMap error: {info}", file=sys.stderr)
                return {}
            return payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"  ! request failed ({exc}), retry {attempt + 1}/{retries}",
                  file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    return {}


def _grid_cells(grid: int) -> list[str]:
    """Split the district bbox into grid*grid rectangles as AMap polygon strings."""
    lng_min, lat_min, lng_max, lat_max = DISTRICT_BBOX
    dlng = (lng_max - lng_min) / grid
    dlat = (lat_max - lat_min) / grid
    cells = []
    for i in range(grid):
        for j in range(grid):
            x1 = lng_min + i * dlng
            y1 = lat_min + j * dlat
            x2 = x1 + dlng
            y2 = y1 + dlat
            # polygon expects "lng1,lat1|lng2,lat2" as opposite corners.
            cells.append(f"{x1:.6f},{y2:.6f}|{x2:.6f},{y1:.6f}")
    return cells


def collect_category(key: str, meta: dict, api_key: str, grid: int,
                     limit: int, conn=None) -> list[dict]:
    """
    Collect POIs for one product category across the whole grid.

    When `conn` is supplied, every fetched POI is upserted into our own POI
    store immediately (deduped by amap_id, accumulating across runs) and each
    grid cell's yield is logged — so we build a self-owned library and never
    re-pay AMap quota for a POI we already have.
    """
    found: dict[str, dict] = {}
    for cell_index, polygon in enumerate(_grid_cells(grid), start=1):
        cell_found = 0
        cell_new = 0
        for page in range(1, MAX_PAGE + 1):
            if len(found) >= limit:
                break
            params = {
                "key": api_key,
                "polygon": polygon,
                "types": meta["amap_types"],
                "show_fields": SHOW_FIELDS,
                "page_size": PAGE_SIZE,
                "page_num": page,
                "output": "JSON",
            }
            payload = _request(params)
            time.sleep(REQUEST_INTERVAL)
            pois = payload.get("pois") or []
            if not pois:
                break
            for poi in pois:
                poi_id = poi.get("id")
                if not poi_id or poi_id in found:
                    continue
                normalised = _normalise(poi, key)
                found[poi_id] = normalised
                cell_found += 1
                if conn is not None:
                    # Persist to the POI library right away; is_new tells us how
                    # much of this cell was fresh vs. already-known data.
                    if db.upsert_poi(conn, normalised, source="amap"):
                        cell_new += 1
            if len(pois) < PAGE_SIZE:
                break
        if conn is not None:
            conn.commit()
            db.log_collect(conn, key, polygon, cell_found, cell_new)
        print(f"  cell {cell_index}/{grid * grid}: {len(found)} unique so far"
              + (f"  (+{cell_new} new to library)" if conn is not None else ""))
        if len(found) >= limit:
            break
    return list(found.values())


def _as_text(value) -> str:
    """AMap returns [] for missing fields instead of null or ''."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _normalise(poi: dict, category: str) -> dict:
    """
    Keep the factual fields we need downstream.

    In v5 the business fields live under `business`, not `biz_ext`.
    `tag` (real dish/feature keywords) and photo titles are the highest-value
    fields here: concrete, verifiable and unique per venue, which is what lets
    the copy be grounded instead of generic.
    """
    location = _as_text(poi.get("location"))
    lng, lat = (location.split(",") + ["", ""])[:2] if location else ("", "")
    business = poi.get("business") or {}
    photos = poi.get("photos") or []
    photo_titles = [_as_text(p.get("title")) for p in photos]
    return {
        "id": _as_text(poi.get("id")),
        "name": _as_text(poi.get("name")),
        "category": category,
        "amap_type": _as_text(poi.get("type")),
        "typecode": _as_text(poi.get("typecode")),
        "address": _as_text(poi.get("address")),
        "adname": _as_text(poi.get("adname")),
        "business_area": _as_text(business.get("business_area")),
        "lng": float(lng) if lng else None,
        "lat": float(lat) if lat else None,
        "tel": _as_text(business.get("tel")),
        # opentime_today is more actionable than the weekly description.
        "opentime": (_as_text(business.get("opentime_today"))
                     or _as_text(business.get("opentime_week"))),
        "rating": _as_text(business.get("rating")),
        "cost": _as_text(business.get("cost")),
        "tag": _as_text(business.get("tag")),
        "alias": _as_text(business.get("alias")),
        "photo_titles": [t for t in photo_titles if t],
        "photo_count": len(photos),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect 朝阳区 POIs from AMap")
    parser.add_argument("--grid", type=int, default=4,
                        help="grid granularity per axis (4 => 16 cells)")
    parser.add_argument("--per-cat", type=int, default=400,
                        help="max POIs to keep per category")
    parser.add_argument("--no-db", action="store_true",
                        help="do not persist into the POI library (JSON only)")
    args = parser.parse_args()

    api_key = os.environ.get("AMAP_KEY", "").strip()
    if not api_key:
        print("ERROR: AMAP_KEY is not set.\n"
              "  1. Register at https://lbs.amap.com/dev/key/app\n"
              "  2. Create a key of type 'Web服务'\n"
              "  3. export AMAP_KEY=your_key", file=sys.stderr)
        return 1

    # Open (and initialise) the POI library unless explicitly disabled. Every
    # fetched POI is upserted here so re-runs accumulate instead of re-fetching.
    conn = None
    if not args.no_db:
        conn = db.connect()
        db.init_db(conn)
        before = db.count_pois(conn).get("total", 0)
        print(f"POI library: {before} POIs before this run\n")

    all_pois: list[dict] = []
    for key, meta in CATEGORIES.items():
        print(f"[{key}] {meta['label']} ...")
        pois = collect_category(key, meta, api_key, args.grid, args.per_cat,
                                conn=conn)
        print(f"[{key}] collected {len(pois)}")
        all_pois.extend(pois)

    # Keep writing raw_poi.json so the existing clean.py step still works
    # unchanged; the database is additive, not a replacement (yet).
    out = Path(RAW_POI_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_pois, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nSaved {len(all_pois)} raw POIs -> {out}")

    if conn is not None:
        counts = db.count_pois(conn)
        print(f"POI library now holds {counts.get('total', 0)} POIs: "
              + ", ".join(f"{k}={v}" for k, v in counts.items() if k != "total"))
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
