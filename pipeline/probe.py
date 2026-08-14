"""
Step 0 - Probe what AMap actually returns, per category.

Why this exists
---------------
The docs say `rating`, `cost` and `tag` are returned only for certain POI
types, and say nothing about how often they are actually populated. Designing
a copy system around fields that turn out to be 5% filled would waste a full
generation run.

So: measure first, design second. This script samples a few POIs per category
and reports the real fill rate of every field we care about.

Usage:
    export AMAP_KEY=your_key
    python3 pipeline/probe.py
    python3 pipeline/probe.py --sample 50 --dump   # also dump one raw record
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
from pipeline.config import CATEGORIES, DISTRICT_ADCODE  # noqa: E402

# v5 search, which supports show_fields. v3 (used previously) cannot return
# tag / photos / opentime_today at all.
API_URL = "https://restapi.amap.com/v5/place/text"
SHOW_FIELDS = "business,photos,children,indoor,navi"

# Fields worth measuring, grouped for a readable report.
PROBE_FIELDS = [
    ("business.tag", "特色内容（仅美食）"),
    ("business.rating", "评分（餐饮/酒店/景点/影院）"),
    ("business.cost", "人均消费"),
    ("business.opentime_today", "今日营业时间"),
    ("business.opentime_week", "营业时间描述"),
    ("business.business_area", "所属商圈"),
    ("business.alias", "别名"),
    ("business.tel", "电话"),
    ("business.keytag", "POI标识"),
    ("business.rectag", "二次确认标识"),
    ("photos", "图片（含title）"),
    ("photos_with_title", "图片标题非空"),
    ("children", "子POI"),
    ("indoor.floor", "所在楼层"),
    ("navi.entr_location", "入口坐标"),
]


def request(params: dict, retries: int = 3) -> dict:
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    delay = 1.0
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("status") != "1":
                info = payload.get("info", "UNKNOWN")
                if any(k in info for k in ("LIMIT", "BUSY", "TIMEOUT")):
                    time.sleep(delay)
                    delay *= 2
                    continue
                print(f"  ! AMap error: {info}", file=sys.stderr)
                return {}
            return payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"  ! {exc}", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    return {}


def has_value(poi: dict, path: str) -> bool:
    """Is a dotted field present and non-empty? AMap uses [] for 'missing'."""
    if path == "photos":
        return bool(poi.get("photos"))
    if path == "photos_with_title":
        return any((p.get("title") or "").strip() for p in poi.get("photos") or [])
    if path == "children":
        return bool(poi.get("children"))

    node: object = poi
    for part in path.split("."):
        if not isinstance(node, dict):
            return False
        node = node.get(part)
    if node is None:
        return False
    if isinstance(node, str):
        return bool(node.strip())
    if isinstance(node, (list, dict)):
        return bool(node)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=25,
                        help="POIs to sample per category")
    parser.add_argument("--dump", action="store_true",
                        help="print one full raw record per category")
    args = parser.parse_args()

    api_key = os.environ.get("AMAP_KEY", "").strip()
    if not api_key:
        print("ERROR: AMAP_KEY not set.\n"
              "  Get one at https://console.amap.com/dev/key/app (type: Web服务)\n"
              "  Then: export AMAP_KEY=your_key", file=sys.stderr)
        return 1

    print(f"Probing AMap v5 fields, {args.sample} POIs per category")
    print(f"show_fields={SHOW_FIELDS}\n")

    overall: dict[str, int] = {path: 0 for path, _ in PROBE_FIELDS}
    overall_total = 0

    for key, meta in CATEGORIES.items():
        payload = request({
            "key": api_key,
            "types": meta["amap_types"],
            "region": DISTRICT_ADCODE,
            "city_limit": "true",
            "show_fields": SHOW_FIELDS,
            "page_size": min(args.sample, 25),
            "page_num": 1,
        })
        pois = payload.get("pois") or []
        time.sleep(0.35)

        if not pois:
            print(f"[{key}] {meta['label']}: no results\n")
            continue

        counts = {path: 0 for path, _ in PROBE_FIELDS}
        for poi in pois:
            for path, _ in PROBE_FIELDS:
                if has_value(poi, path):
                    counts[path] += 1
                    overall[path] += 1
        overall_total += len(pois)

        print(f"[{key}] {meta['label']}  (n={len(pois)})")
        for path, label in PROBE_FIELDS:
            filled = counts[path]
            if filled == 0:
                continue
            pct = filled / len(pois)
            bar = "#" * int(pct * 20)
            print(f"    {path:26s} {filled:3d}/{len(pois):<3d} {pct:5.0%} {bar}  {label}")

        # Show a concrete example of the highest-value field.
        for poi in pois:
            tag = (poi.get("business") or {}).get("tag")
            if isinstance(tag, str) and tag.strip():
                print(f"    e.g. tag of 「{poi.get('name')}」: {tag[:80]}")
                break
        print()

        if args.dump:
            print("--- raw sample ---")
            print(json.dumps(pois[0], ensure_ascii=False, indent=2)[:1500])
            print("--- end ---\n")

    if overall_total:
        print("=" * 62)
        print(f"OVERALL fill rate across {overall_total} POIs")
        for path, label in PROBE_FIELDS:
            pct = overall[path] / overall_total
            verdict = "USABLE" if pct >= 0.5 else "SPARSE" if pct >= 0.15 else "TOO RARE"
            print(f"  {path:26s} {pct:5.0%}  {verdict:9s} {label}")
        print("\nRule of thumb: only build required copy logic on USABLE fields.")
        print("SPARSE fields may be used opportunistically (when present).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
