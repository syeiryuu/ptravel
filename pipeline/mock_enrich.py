"""
Inject simulated enrichment data into clean_poi.json.

This is the offline counterpart of enrich.py - it produces the same shape of
data (poi_density, aoi_name, moon_phase, sunset, wiki_description, inception,
is_heritage, dynasty) but from deterministic rules rather than live API calls,
so the generate step can produce differentiated copy without spending quota.

The values are plausible but synthetic. When real API keys are available,
run enrich.py instead - it will overwrite these fields with real data.

Usage:
    python3 pipeline/mock_enrich.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import CLEAN_POI_FILE  # noqa: E402

# Deterministic RNG so the same input always produces the same enrichment.
rng = random.Random(42)

# Named AOIs in 朝阳区, mapped to approximate area keys.
# A POI whose business_area matches gets assigned to the AOI.
AOI_MAP = {
    "酒仙桥": "798艺术区",
    "望京": "望京SOHO",
    "三里屯": "三里屯太古里",
    "朝阳公园": "朝阳公园",
    "亮马桥": "亮马河风情水岸",
    "国贸": "国贸商城",
    "双井": "合生汇",
    "青年路": "大悦城",
}

# Wikipedia-style descriptions for notable place types.
WIKI_DESCRIPTIONS = {
    "park": ["北京市朝阳区的一座城市公园", "北京市的公园绿地",
             "朝阳区的休闲公园", "北京市区内的开放式公园"],
    "culture": ["北京市朝阳区的美术馆", "北京市的私营美术馆",
                "朝阳区的当代艺术空间", "北京市的文化场馆"],
    "weird": ["北京市朝阳区的创意园区", "由旧工业建筑改造的文化空间",
              "朝阳区的艺术区", "北京市的非标商业空间"],
    "night": ["北京市朝阳区的现场音乐场所", "朝阳区的酒吧",
              "北京市的夜间娱乐场所"],
}

# Parks and cultural venues that might have heritage status or dynasty info.
HERITAGE_CHANCE = {"park": 0.15, "culture": 0.10, "weird": 0.05}
DYNASTY_CHANCE = {"park": 0.10, "culture": 0.05, "weird": 0.03}
INCEPTION_CHANCE = {"park": 0.30, "culture": 0.20, "weird": 0.15,
                    "night": 0.10, "shop": 0.08, "cafe": 0.05, "food": 0.03}

DYNASTIES = ["明朝", "清代", "民国年间", "建国初期"]

# Moon phase for "today" - a fixed plausible value for the data build.
MOON_PHASE = "盈凸月"
SUNSET = "19:24"


def enrich_poi(poi: dict) -> dict:
    """Add enrichment fields to a single POI, in-place."""
    category = poi.get("category", "")
    name = poi.get("name", "")
    area = poi.get("business_area", "")

    # --- regeo: poi_density, lively_count, aoi_name ---
    poi_density = rng.randint(5, 55)
    # Food/cafe/shop areas are livelier.
    lively_base = {"food": 0.6, "cafe": 0.5, "shop": 0.5, "night": 0.4,
                   "park": 0.15, "culture": 0.2, "weird": 0.2}
    lively_count = int(poi_density * lively_base.get(category, 0.3)
                       * rng.uniform(0.5, 1.0))
    poi["poi_density"] = poi_density
    poi["lively_count"] = lively_count

    aoi_name = AOI_MAP.get(area)
    if aoi_name and rng.random() < 0.45:
        poi["aoi_name"] = aoi_name

    # --- qweather: moon_phase, sunset ---
    poi["moon_phase"] = MOON_PHASE
    # Sunset signal only matters for outdoor-ish categories.
    if category in ("park", "weird", "night"):
        poi["sunset"] = SUNSET

    # --- wikipedia: wiki_description, inception, is_heritage, dynasty ---
    if category in WIKI_DESCRIPTIONS and rng.random() < 0.45:
        poi["wiki_description"] = rng.choice(WIKI_DESCRIPTIONS[category])

    if rng.random() < INCEPTION_CHANCE.get(category, 0.05):
        poi["inception"] = str(rng.randint(1920, 2010))

    if rng.random() < HERITAGE_CHANCE.get(category, 0):
        poi["is_heritage"] = True

    if rng.random() < DYNASTY_CHANCE.get(category, 0):
        poi["dynasty"] = rng.choice(DYNASTIES)

    return poi


def main() -> int:
    path = Path(CLEAN_POI_FILE)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 1

    pois = json.loads(path.read_text(encoding="utf-8"))
    print(f"Enriching {len(pois)} POIs with simulated data ...")

    for poi in pois:
        enrich_poi(poi)

    path.write_text(json.dumps(pois, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    # Report stats
    stats = {
        "poi_density": sum(1 for p in pois if p.get("poi_density") is not None),
        "aoi_name": sum(1 for p in pois if p.get("aoi_name")),
        "moon_phase": sum(1 for p in pois if p.get("moon_phase")),
        "sunset": sum(1 for p in pois if p.get("sunset")),
        "wiki_description": sum(1 for p in pois if p.get("wiki_description")),
        "inception": sum(1 for p in pois if p.get("inception")),
        "is_heritage": sum(1 for p in pois if p.get("is_heritage")),
        "dynasty": sum(1 for p in pois if p.get("dynasty")),
    }
    print("Enrichment stats:")
    for key, count in stats.items():
        print(f"  {key:20s}: {count:5d} ({count/len(pois):.0%})")
    print(f"Updated -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
