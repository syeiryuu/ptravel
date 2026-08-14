"""
Signal coverage check - does every category have enough to write from?

generate.py refuses to write copy for a POI with fewer than MIN_SIGNALS
verified facts. That rule protects us from fiction, but it also means a
category with thin data silently disappears from the gacha - and category
rotation is the mechanic the whole product rests on.

This script answers the only question that matters before spending API money:
**would each category survive generation?**

Usage:
    python3 pipeline/verify_signals.py                     # real clean_poi.json
    python3 pipeline/verify_signals.py --mock              # fixtures, pre-enrich
    python3 pipeline/verify_signals.py --mock --simulate-enrich
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import CATEGORIES, CLEAN_POI_FILE  # noqa: E402
from pipeline.signals import build_signals  # noqa: E402

MIN_SIGNALS = 2

# Observed / expected hit rates for each enrichment source, used only by
# --simulate-enrich to forecast coverage before spending real quota.
#   regeo  : ~always succeeds, we have coordinates for every POI
#   wiki   : only notable places have articles; parks and museums do far more
#            often than a古着店 does
WIKI_HIT_RATE = {
    "park": 0.55, "culture": 0.45, "weird": 0.15,
    "night": 0.10, "shop": 0.05, "cafe": 0.02, "food": 0.02,
}


def simulate_enrichment(pois: list[dict]) -> list[dict]:
    """
    Apply plausible enrichment outcomes so we can forecast coverage.

    This is a *forecast*, not data - it never writes anywhere and is only
    used to decide whether running the real enrichment is worth it.
    """
    rng = random.Random(42)
    out = []
    for poi in pois:
        record = dict(poi)
        # regeo: succeeds whenever we have coordinates.
        if record.get("lng") is not None:
            record["poi_density"] = rng.randint(3, 60)
            record["lively_count"] = rng.randint(0, record["poi_density"])
            if rng.random() < 0.35:
                record["aoi_name"] = rng.choice(
                    ["798艺术区", "三里屯太古里", "朝阳公园", "郎园Station",
                     "颐堤港", "合生汇"]
                )
        # qweather: district-wide, so it lands on everything.
        record["moon_phase"] = "盈凸月"
        record["sunset"] = "19:24"
        # wikipedia: category-dependent.
        if rng.random() < WIKI_HIT_RATE.get(record.get("category", ""), 0.05):
            record["wiki_description"] = "北京市朝阳区的一处场所"
            if rng.random() < 0.6:
                record["inception"] = str(rng.randint(1920, 2005))
            if rng.random() < 0.2:
                record["is_heritage"] = True
        out.append(record)
    return out


def summarise(pois: list[dict]) -> int:
    by_cat: dict[str, list[int]] = defaultdict(list)
    source_use: Counter = Counter()
    starved: list[tuple[str, str, int]] = []

    for poi in pois:
        signals = build_signals(poi)
        category = poi.get("category", "?")
        by_cat[category].append(len(signals))
        for signal in signals:
            source_use[signal["source"]] += 1
        if len(signals) < MIN_SIGNALS:
            starved.append((category, poi.get("name", "?"), len(signals)))

    print(f"{'category':10s} {'n':>5s} {'avg':>6s} {'min':>4s} "
          f"{'>=2':>6s}  status")
    print("-" * 52)
    failing = 0
    for category in CATEGORIES:
        counts = by_cat.get(category)
        if not counts:
            print(f"{category:10s} {0:5d}      -    -      -  NO DATA")
            failing += 1
            continue
        usable = sum(1 for c in counts if c >= MIN_SIGNALS)
        ratio = usable / len(counts)
        avg = sum(counts) / len(counts)
        status = "OK" if ratio >= 0.8 else ("THIN" if ratio >= 0.5 else "STARVED")
        if status != "OK":
            failing += 1
        print(f"{category:10s} {len(counts):5d} {avg:6.1f} {min(counts):4d} "
              f"{ratio:5.0%}  {status}")

    print("\nsignal source usage")
    print("-" * 52)
    total = len(pois) or 1
    for source, count in source_use.most_common():
        print(f"  {source:22s} {count:5d}  ({count / total:.0%} of POIs)")

    if starved:
        print(f"\n{len(starved)} POI(s) below the {MIN_SIGNALS}-signal floor "
              f"(these would be skipped):")
        for category, name, count in starved[:10]:
            print(f"  [{category}] {name} - {count} signal(s)")
        if len(starved) > 10:
            print(f"  ... and {len(starved) - 10} more")

    return failing


def main() -> int:
    parser = argparse.ArgumentParser(description="Check signal coverage")
    parser.add_argument("--mock", action="store_true",
                        help="use generated fixtures instead of real data")
    parser.add_argument("--simulate-enrich", action="store_true",
                        help="forecast coverage after enrich.py runs")
    args = parser.parse_args()

    if args.mock:
        from pipeline.mock_poi import build_mock_pois

        pois = build_mock_pois()
        print(f"Using {len(pois)} mock POIs")
    else:
        path = Path(CLEAN_POI_FILE)
        if not path.exists():
            print(f"ERROR: {path} not found. Run collect.py + clean.py, "
                  f"or pass --mock.", file=sys.stderr)
            return 1
        pois = json.loads(path.read_text(encoding="utf-8"))
        print(f"Using {len(pois)} POIs from {path}")

    if args.simulate_enrich:
        pois = simulate_enrichment(pois)
        print("(forecasting post-enrichment coverage - not real data)")
    print()

    failing = summarise(pois)
    if failing:
        print(f"\n{failing} category/categories are not generation-ready. "
              f"Run pipeline/enrich.py to add signals.")
    else:
        print("\nAll categories are generation-ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
