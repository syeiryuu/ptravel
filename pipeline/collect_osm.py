"""
Step 1 (Dolomites) - Collect POI skeletons from OpenStreetMap via Overpass.

AMap only covers mainland China, so the Beijing pipeline cannot see the Alps.
For 多洛米蒂 we use OpenStreetMap instead: it is global, free, needs no key,
and its licence (ODbL) lets us take *objective facts* (name, coordinates, type,
opening hours, elevation) — exactly the same class of facts we take from AMap.
All subjective copy is still generated later by us.

The output of `_normalise_osm` is intentionally identical in shape to
collect._normalise, so clean / signals / generate / the DB layer all work
unchanged. Fields OSM does not carry (rating / cost / photos) are left empty;
the signal layer already skips missing fields gracefully.

Usage:
    export PTRAVEL_REGION=dolomites
    python3 pipeline/collect_osm.py [--per-cat 300]
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import (  # noqa: E402
    CATEGORIES,
    DISTRICT_BBOX,
    DISTRICT_NAME,
    RAW_POI_FILE,
)
from pipeline.db import database as db  # noqa: E402

# Public Overpass endpoints, tried in order so one overloaded mirror does not
# kill a run. kumi.systems first because the main de instance rate-limits hard
# after a burst of probing; de stays as a fallback.
OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
# Overpass rejects requests without a User-Agent (HTTP 406), so always send one.
USER_AGENT = "ptravel-poi-collector/1.0 (personal hobby project)"
REQUEST_INTERVAL = 3.0  # be polite to the shared public endpoints


def _bbox_str() -> str:
    """Overpass wants bbox as (south,west,north,east)."""
    lng_min, lat_min, lng_max, lat_max = DISTRICT_BBOX
    return f"{lat_min},{lng_min},{lat_max},{lng_max}"


def _selector(tags: dict) -> str:
    """Render one tag dict as an Overpass filter, e.g. {'tourism':'peak'}."""
    parts = []
    for key, value in tags.items():
        if value is True:
            parts.append(f'["{key}"]')
        else:
            parts.append(f'["{key}"="{value}"]')
    return "".join(parts)


def _build_query(osm_selectors: list[dict], bbox: str) -> str:
    """
    One Overpass QL query for a category: union of node/way/relation matching
    any of the selectors, with centroids for ways/relations so everything has
    a single lat/lon we can use as the POI location.
    """
    body_lines = []
    for sel in osm_selectors:
        filt = _selector(sel)
        body_lines.append(f"  nwr{filt}({bbox});")
    body = "\n".join(body_lines)
    return f"[out:json][timeout:60];\n(\n{body}\n);\nout center tags;"


def _request(query: str, retries: int = 3) -> dict:
    """POST an Overpass query, trying each mirror with backoff. {} on failure."""
    data = urllib.parse.urlencode({"data": query}).encode()
    delay = 2.0
    for attempt in range(retries):
        endpoint = OVERPASS_ENDPOINTS[attempt % len(OVERPASS_ENDPOINTS)]
        req = urllib.request.Request(
            endpoint, data=data, headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                json.JSONDecodeError, OSError) as exc:
            # socket.timeout is not a TimeoutError alias on Python 3.9, and a
            # dropped connection surfaces as a bare OSError — catch them all so
            # one slow mirror never aborts the whole run.
            print(f"  ! Overpass {endpoint} failed ({exc}); "
                  f"retry {attempt + 1}/{retries}", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    return {}


# --- OSM element -> our normalised POI shape -------------------------------

def _pick_name(tags: dict) -> str:
    """Prefer the local Italian/German name, then the default OSM name."""
    for key in ("name:it", "name", "name:de", "int_name", "name:en"):
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return ""


def _compose_address(tags: dict) -> str:
    """Build a human address from addr:* tags (OSM has no single address field)."""
    parts = [
        tags.get("addr:street", ""),
        tags.get("addr:housenumber", ""),
        tags.get("addr:city", ""),
        tags.get("addr:postcode", ""),
    ]
    return " ".join(p for p in parts if p).strip()


# OSM cuisine values are English keys; translate the common Alpine ones so the
# copy reads naturally in Chinese. Unknown values fall through untranslated.
_CUISINE_ZH = {
    "regional": "本地菜",
    "italian": "意大利菜",
    "pizza": "披萨",
    "pasta": "手工意面",
    "coffee shop": "咖啡",
    "cafe": "咖啡",
    "ice cream": "冰淇淋",
    "dessert": "甜点",
    "german": "德式菜",
    "austrian": "奥地利菜",
    "bavarian": "巴伐利亚菜",
    "local": "本地风味",
    "international": "各国菜",
    "mediterranean": "地中海菜",
    "tyrolean": "蒂罗尔菜",
    "steak_house": "牛排",
    "barbecue": "烤肉",
}


def _compose_tag(tags: dict) -> str:
    """
    Pack OSM's high-value descriptor tags into the comma-separated `tag` field
    the signal layer already knows how to read (like AMap's dish keywords).

    We include only *concrete, verifiable* attributes: cuisine, elevation,
    what kind of mountain hut it is, the aerialway type, etc.
    """
    out: list[str] = []
    cuisine = tags.get("cuisine")
    if cuisine:
        for raw in cuisine.split(";"):
            key = raw.strip().lower().replace("_", " ")
            if key:
                out.append(_CUISINE_ZH.get(key, key))
    # NB: elevation is intentionally NOT added here — it already flows through
    # the dedicated `alias` field and signal_elevation, so putting it in `tag`
    # too would produce a duplicate "海拔…" signal in the copy.
    if tags.get("tourism") == "alpine_hut":
        out.append("山间小屋")
    if tags.get("tourism") == "wilderness_hut":
        out.append("无人山棚")
    aerialway = tags.get("aerialway")
    if aerialway in ("cable_car", "gondola"):
        out.append("缆车" if aerialway == "cable_car" else "贡多拉索道")
    if tags.get("natural") == "peak":
        out.append("山峰")
    if tags.get("mountain_pass") == "yes":
        out.append("垭口")
    if tags.get("waterway") == "waterfall" or tags.get("natural") == "waterfall":
        out.append("瀑布")
    if tags.get("historic") == "castle":
        out.append("城堡")
    # De-dup while preserving order.
    seen: set[str] = set()
    uniq = [t for t in out if not (t in seen or seen.add(t))]
    return ",".join(uniq)


def _elevation_note(tags: dict) -> str:
    ele = tags.get("ele")
    if not ele:
        return ""
    try:
        return f"海拔约{int(float(ele))}米"
    except ValueError:
        return ""


def _normalise_osm(element: dict, category: str) -> dict | None:
    """
    Turn one Overpass element into our POI shape. Returns None when it has no
    name (a copy with no venue name is useless) or no coordinates.
    """
    tags = element.get("tags") or {}
    name = _pick_name(tags)
    if not name:
        return None

    # Ways/relations carry their centroid under `center`; nodes carry lat/lon.
    if element.get("type") == "node":
        lat, lng = element.get("lat"), element.get("lon")
    else:
        center = element.get("center") or {}
        lat, lng = center.get("lat"), center.get("lon")
    if lat is None or lng is None:
        return None

    osm_id = f"osm:{element.get('type')}/{element.get('id')}"
    # `adname` reuses the OSM place/valley if present, else the region name.
    adname = (tags.get("addr:city") or tags.get("addr:suburb")
              or tags.get("addr:village") or DISTRICT_NAME)

    return {
        "id": osm_id,
        "name": name,
        "category": category,
        # amap_type kept for schema compatibility; we store the OSM class here.
        "amap_type": _osm_class(tags),
        "typecode": "",
        "address": _compose_address(tags),
        "adname": adname,
        # business_area == the valley / resort area, used by signal_area.
        "business_area": (tags.get("addr:city") or tags.get("addr:village") or ""),
        "lng": float(lng),
        "lat": float(lat),
        "tel": (tags.get("phone") or tags.get("contact:phone") or "").strip(),
        "opentime": (tags.get("opening_hours") or "").strip(),
        # OSM carries no ratings/prices; leave empty so signals skip them.
        "rating": "",
        "cost": "",
        "tag": _compose_tag(tags),
        # Stash the raw website/elevation as alias-ish extra; alias is free text.
        "alias": _elevation_note(tags),
        "photo_titles": [],
        "photo_count": 0,
    }


def _osm_class(tags: dict) -> str:
    """A short human label of what this OSM element is (for debugging/audit)."""
    for key in ("tourism", "natural", "aerialway", "historic", "amenity",
                "waterway", "place"):
        if tags.get(key):
            return f"{key}={tags[key]}"
    return ""


# --- Collection ------------------------------------------------------------

def collect_category(key: str, meta: dict, limit: int, conn=None) -> list[dict]:
    """Collect one product category for the whole Dolomites bbox in one query."""
    query = _build_query(meta["osm"], _bbox_str())
    payload = _request(query)
    time.sleep(REQUEST_INTERVAL)
    elements = payload.get("elements") or []

    found: dict[str, dict] = {}
    new_count = 0
    for element in elements:
        normalised = _normalise_osm(element, key)
        if normalised is None:
            continue
        poi_id = normalised["id"]
        if poi_id in found:
            continue
        found[poi_id] = normalised
        if conn is not None:
            if db.upsert_poi(conn, normalised, source="osm"):
                new_count += 1
        if len(found) >= limit:
            break

    if conn is not None:
        conn.commit()
        db.log_collect(conn, key, "dolomites-bbox", len(found), new_count)
    print(f"  fetched {len(elements)} OSM elements -> {len(found)} named POIs"
          + (f"  (+{new_count} new to library)" if conn is not None else ""))
    return list(found.values())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect Dolomites POIs from OpenStreetMap (Overpass)")
    parser.add_argument("--per-cat", type=int, default=300,
                        help="max POIs to keep per category")
    parser.add_argument("--no-db", action="store_true",
                        help="do not persist into the POI library (JSON only)")
    args = parser.parse_args()

    conn = None
    if not args.no_db:
        conn = db.connect()
        db.init_db(conn)
        before = db.count_pois(conn).get("total", 0)
        print(f"POI library: {before} POIs before this run\n")

    all_pois: list[dict] = []
    for key, meta in CATEGORIES.items():
        print(f"[{key}] {meta['label']} ...")
        try:
            pois = collect_category(key, meta, args.per_cat, conn=conn)
        except Exception as exc:  # noqa: BLE001
            # Keep going: a single failed category should not lose the others,
            # and the DB upserts already committed remain intact.
            print(f"[{key}] FAILED ({exc}); skipping", file=sys.stderr)
            continue
        print(f"[{key}] collected {len(pois)}")
        all_pois.extend(pois)

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
