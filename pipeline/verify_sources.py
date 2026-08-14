"""
Live smoke test for the enrichment sources.

Each source is a network dependency we do not control, so before running the
full pipeline it is worth confirming three things:
  1. it still responds in the shape we expect
  2. it matches the *right* entity (a wrong Wikipedia match produces copy that
     is confidently false - the worst possible failure for this product)
  3. missing credentials degrade instead of crashing

Usage:
    python3 pipeline/verify_sources.py            # wikipedia only (no key)
    python3 pipeline/verify_sources.py --all      # also regeo + qweather
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.signals import build_signals  # noqa: E402
from pipeline.sources import amap_regeo, qweather, wikipedia  # noqa: E402

# Real 朝阳区 places spanning our weak categories, plus one that should NOT
# match anything - a false positive there would be a serious bug.
WIKI_SAMPLES = [
    ("朝阳公园", "park"),
    ("中国紫檀博物馆", "culture"),
    ("北京民生现代美术馆", "culture"),
    ("798艺术区", "weird"),
    ("日坛公园", "park"),
    ("拐角空间·望京店77", "weird"),   # synthetic: must return None
]


def check_wikipedia() -> None:
    print("=== zh.wikipedia / wikidata ===")
    for name, category in WIKI_SAMPLES:
        result = wikipedia.lookup(name)
        if not result:
            print(f"  {name:20s} -> no match")
            continue
        bits = [f"title={result.get('wiki_title')}"]
        if result.get("inception"):
            bits.append(f"inception={result['inception']}")
        if result.get("is_heritage"):
            bits.append("heritage=yes")
        if result.get("wiki_description"):
            bits.append(f"desc={result['wiki_description'][:20]}")
        print(f"  {name:20s} -> {', '.join(bits)}")

        # Show what the signal layer makes of it - the actual deliverable.
        poi = {"name": name, "category": category, **result}
        for signal in build_signals(poi):
            if signal["source"].startswith("wiki"):
                print(f"      [{signal['source']}] {signal['fact']}")


def check_regeo() -> None:
    print("\n=== amap regeo ===")
    if not os.environ.get("AMAP_KEY", "").strip():
        print("  AMAP_KEY not set - skipped (this is a graceful degrade)")
        return
    samples = [
        ("798艺术区", 116.4959, 39.9843),
        ("三里屯", 116.4551, 39.9370),
        ("将府公园", 116.5203, 39.9755),
    ]
    for name, lng, lat in samples:
        result = amap_regeo.surroundings(lng, lat)
        if not result:
            print(f"  {name:12s} -> failed")
            continue
        print(f"  {name:12s} -> density={result['poi_density']} "
              f"lively={result['lively_count']} aoi={result.get('aoi_name')}")


def check_qweather() -> None:
    print("\n=== qweather ===")
    if not qweather.is_configured():
        print("  QWEATHER_* not set - skipped (this is a graceful degrade)")
        return
    today = datetime.now().strftime("%Y%m%d")
    moon = qweather.moon_phase(today)
    print(f"  moon   -> {moon}")
    sun = qweather.sun_times(today)
    print(f"  sun    -> {sun}")
    now = qweather.current_weather()
    print(f"  now    -> {now}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test enrichment sources")
    parser.add_argument("--all", action="store_true",
                        help="also test the credentialed sources")
    args = parser.parse_args()

    check_wikipedia()
    if args.all:
        check_regeo()
        check_qweather()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
