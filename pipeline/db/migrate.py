"""
One-off migration: load existing JSON files into the SQLite POI database.

This backfills the self-owned store from whatever the pipeline has already
produced, so switching to the database costs no AMap quota:

    raw_poi.json   -> poi            (the factual POI skeletons)
    clean_poi.json -> poi + poi_copy (adds open/close hours, direction, rarity)
    gacha.json     -> poi_copy       (the LLM copy: hook/reason/oracle/action)

It is idempotent: re-running upserts the same rows without duplication. The
`source` is tagged 'mock' when ids look synthetic (MOCK...), so real AMap data
can later be told apart from the development fixtures.

Usage:
    python3 -m pipeline.db.migrate
    python3 -m pipeline.db.migrate --only gacha
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pipeline.config import (  # noqa: E402
    CLEAN_POI_FILE,
    OUTPUT_FILE,
    RAW_POI_FILE,
)
from pipeline.db import database as db  # noqa: E402


def _load_json(path_str: str) -> list[dict]:
    path = Path(db._project_root() / path_str)
    if not path.exists():
        print(f"  (skip) {path_str} not found")
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _source_for(poi: dict) -> str:
    """Tag synthetic fixtures so we can distinguish them from real AMap data."""
    pid = str(poi.get("id") or poi.get("amap_id") or "")
    return "mock" if pid.upper().startswith("MOCK") else "amap"


def migrate_raw(conn) -> None:
    rows = _load_json(RAW_POI_FILE)
    if not rows:
        return
    print(f"raw_poi.json: {len(rows)} rows")
    new = 0
    for poi in rows:
        if db.upsert_poi(conn, poi, source=_source_for(poi)):
            new += 1
    conn.commit()
    print(f"  upserted {len(rows)} POIs ({new} new)")


def migrate_clean(conn) -> None:
    """clean_poi.json carries the factual POI *and* the derived clean fields."""
    rows = _load_json(CLEAN_POI_FILE)
    if not rows:
        return
    print(f"clean_poi.json: {len(rows)} rows")
    new = 0
    for poi in rows:
        # The clean file is a superset of raw, so upsert the POI too — this
        # lets migration work even when raw_poi.json is absent.
        if db.upsert_poi(conn, poi, source=_source_for(poi)):
            new += 1
        # Derived clean fields (no LLM copy yet): store what we have.
        db.upsert_poi_copy(conn, {
            "amap_id": poi.get("id") or poi.get("amap_id"),
            "open_hour": poi.get("open_hour"),
            "close_hour": poi.get("close_hour"),
            "duration_minutes": poi.get("duration_minutes"),
            "direction": poi.get("direction"),
            "rarity": poi.get("rarity", "common"),
            "lucky": poi.get("lucky", 50),
            # copy fields stay empty until generate/gacha migration fills them
            "hook": "", "reason": "", "oracle": "", "action": "",
            "sources": [],
        })
    conn.commit()
    print(f"  upserted {len(rows)} POIs + clean fields ({new} new POIs)")


def migrate_gacha(conn) -> None:
    """gacha.json is the app-facing output: POI facts + final LLM copy."""
    rows = _load_json(OUTPUT_FILE)
    if not rows:
        return
    print(f"gacha.json: {len(rows)} rows")
    new = 0
    for rec in rows:
        amap_id = rec.get("id") or rec.get("amap_id")
        # Ensure the POI row exists (gacha.json is trimmed, but has the basics).
        if db.upsert_poi(conn, {
            "amap_id": amap_id,
            "name": rec.get("name", ""),
            "category": rec.get("category", ""),
            "amap_type": rec.get("categoryLabel", ""),
            "address": rec.get("address", ""),
            "business_area": rec.get("area", ""),
            "lng": rec.get("lng"),
            "lat": rec.get("lat"),
        }, source=_source_for(rec)):
            new += 1
        # The full copy (this is the important part gacha.json uniquely has).
        db.upsert_poi_copy(conn, {
            "amap_id": amap_id,
            "open_hour": rec.get("openHour"),
            "close_hour": rec.get("closeHour"),
            "duration_minutes": rec.get("durationMinutes"),
            "direction": rec.get("direction"),
            "rarity": rec.get("rarity", "common"),
            "lucky": rec.get("lucky", 50),
            "hook": rec.get("hook", ""),
            "reason": rec.get("reason", ""),
            "oracle": rec.get("oracle", ""),
            "action": rec.get("action", ""),
            "sources": rec.get("sources", []),
        })
    conn.commit()
    print(f"  upserted {len(rows)} copies ({new} new POIs)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate JSON files into SQLite")
    parser.add_argument("--only", choices=["raw", "clean", "gacha"],
                        help="migrate a single source only")
    args = parser.parse_args()

    conn = db.connect()
    db.init_db(conn)
    print(f"DB ready at {db._project_root() / db.DB_FILE}\n")

    if args.only == "raw":
        migrate_raw(conn)
    elif args.only == "clean":
        migrate_clean(conn)
    elif args.only == "gacha":
        migrate_gacha(conn)
    else:
        # Full backfill, in dependency order. clean is a superset of raw, and
        # gacha adds the final copy, so running all three is safe & idempotent.
        migrate_raw(conn)
        migrate_clean(conn)
        migrate_gacha(conn)

    print("\nFinal POI counts:", db.count_pois(conn))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
