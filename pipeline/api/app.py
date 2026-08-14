"""
FastAPI service for 下一站扭蛋.

Endpoints
---------
GET  /api/health                       — DB reachability + POI counts
GET  /api/pool?region=&mbti=&preferences=
                                       — the suggestion pool for a profile
POST /api/profile                      — save / update a user's 我的 profile
POST /api/draw                         — record one draw (luck, super flag)

Design
------
* The server is a thin shell over pipeline/db. It never re-implements the gacha
  feel (rotation / session de-dup / weighted pick) — that stays in the frontend
  so a draw stays instant and offline-capable. The server only *hands over a
  good pool* and *logs behaviour*.

* Multi-region without a global env var. pipeline/config resolves paths from
  PTRAVEL_REGION at import time, which is fine for one-shot CLI scripts but not
  for a long-lived server that must serve several regions at once. So this
  module computes each region's DB path itself (mirroring config's _SUFFIX rule)
  and opens the right connection per request.

* Three-level pool fallback, because regions are at different maturities:
    1. recommend_pool for the profile   (Beijing: profile-weighted, offline-built)
    2. all POIs that have copy, weighted live via recommend.weight_for
    3. the static public/data/gacha_{region}.json  (Dolomites: copy lives only
       in the JSON today, not yet in its DB)
  The client therefore always gets a usable pool from one code path.

Run:
    uvicorn pipeline.api.app:app --reload --port 8787
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Make `pipeline` importable when uvicorn is launched from the repo root.
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from pipeline.db import database as db  # noqa: E402
from pipeline.db import recommend  # noqa: E402

app = FastAPI(title="下一站扭蛋 API", version="1.0.0")

# The Vite dev server proxies /api, so same-origin in practice; CORS is opened
# only to keep direct-to-8787 access (curl, a differently-hosted client) simple.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_REGION = "chaoyang"


# ---------------------------------------------------------------------------
# Region -> paths. Mirrors pipeline/config._SUFFIX so a server process can talk
# to any region's DB / static file without relying on the PTRAVEL_REGION env.
# ---------------------------------------------------------------------------
def _suffix(region: str) -> str:
    return "" if region == DEFAULT_REGION else f"_{region}"


def _db_path(region: str) -> Path:
    return _ROOT / f"pipeline/data/ptravel{_suffix(region)}.db"


def _static_pool_path(region: str) -> Path:
    return _ROOT / f"public/data/gacha{_suffix(region)}.json"


def _open(region: str) -> sqlite3.Connection | None:
    """Open a read-friendly connection to a region's DB, or None if absent."""
    path = _db_path(region)
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Category label lookup, per region. We avoid importing config.CATEGORIES
# (which is region-bound at import time) and instead read the two static maps
# directly so one process can label both regions.
# ---------------------------------------------------------------------------
def _category_labels(region: str) -> dict[str, str]:
    # config.DOLOMITES_CATEGORIES is always the mountain map regardless of which
    # region the env selected at import; the Beijing labels are frozen below so
    # they survive a PTRAVEL_REGION=dolomites rebinding of config.CATEGORIES.
    if region == "dolomites":
        from pipeline import config
        cats = config.DOLOMITES_CATEGORIES
    else:
        cats = _BEIJING_CATEGORIES
    return {key: val.get("label", key) for key, val in cats.items()}


# A frozen copy of the Beijing buckets' labels, captured independently of the
# env-driven config.CATEGORIES rebinding.
_BEIJING_CATEGORIES = {
    "cafe": {"label": "咖啡馆 / 茶室"},
    "food": {"label": "餐馆 / 小吃 / 深夜食堂"},
    "park": {"label": "公园 / 步道 / 城市绿地"},
    "culture": {"label": "博物馆 / 美术馆 / 书店"},
    "shop": {"label": "买手店 / 市集 / 古着店"},
    "night": {"label": "livehouse / 酒吧 / 观景点"},
    "weird": {"label": "小众怪地方"},
}


# ---------------------------------------------------------------------------
# snake_case DB row  ->  camelCase Suggestion the frontend's gacha.ts expects.
# Mirrors the payload shape produced by local_generate*.py.
# ---------------------------------------------------------------------------
def _to_suggestion(row: dict, labels: dict[str, str]) -> dict:
    category = row.get("category", "")
    return {
        "id": row.get("amap_id") or row.get("id"),
        "name": row.get("name", ""),
        "category": category,
        "categoryLabel": labels.get(category, category),
        "area": (row.get("business_area") or row.get("adname") or ""),
        "address": row.get("address", ""),
        "lng": row.get("lng"),
        "lat": row.get("lat"),
        "openHour": row.get("open_hour"),
        "closeHour": row.get("close_hour"),
        "durationMinutes": row.get("duration_minutes"),
        "direction": row.get("direction"),
        "rarity": row.get("rarity") or "common",
        "lucky": row.get("lucky") if row.get("lucky") is not None else 50,
        "hook": row.get("hook", ""),
        "reason": row.get("reason", ""),
        "oracle": row.get("oracle", ""),
        "action": row.get("action", ""),
        "sources": row.get("sources", []),
    }


def _rows_with_copy(conn: sqlite3.Connection) -> list[dict]:
    """All POIs that have usable copy, joined into the app-facing shape."""
    rows = conn.execute(
        "SELECT p.amap_id, p.name, p.category, p.address, p.adname, "
        "p.business_area, p.lng, p.lat, p.rating, p.cost, "
        "c.open_hour, c.close_hour, c.duration_minutes, c.direction, "
        "c.rarity, c.lucky, c.hook, c.reason, c.oracle, c.action, c.sources "
        "FROM poi p JOIN poi_copy c ON p.amap_id = c.amap_id "
        "WHERE c.hook IS NOT NULL AND c.hook != ''"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("sources"), str):
            try:
                d["sources"] = json.loads(d["sources"])
            except (json.JSONDecodeError, TypeError):
                d["sources"] = []
        out.append(d)
    return out


def _pool_from_recommend(conn: sqlite3.Connection, key: str) -> list[dict]:
    """Latest recommend_pool batch for a profile key, joined with copy, ranked."""
    rows = conn.execute(
        "SELECT p.amap_id, p.name, p.category, p.address, p.adname, "
        "p.business_area, p.lng, p.lat, "
        "c.open_hour, c.close_hour, c.duration_minutes, c.direction, "
        "c.rarity, c.lucky, c.hook, c.reason, c.oracle, c.action, c.sources, "
        "rp.weight, rp.rank "
        "FROM recommend_pool rp "
        "JOIN poi p ON p.amap_id = rp.amap_id "
        "JOIN poi_copy c ON c.amap_id = rp.amap_id "
        "WHERE rp.profile_key = ? "
        "AND rp.built_at = (SELECT MAX(built_at) FROM recommend_pool "
        "                   WHERE profile_key = ?) "
        "ORDER BY rp.rank ASC",
        (key, key),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("sources"), str):
            try:
                d["sources"] = json.loads(d["sources"])
            except (json.JSONDecodeError, TypeError):
                d["sources"] = []
        out.append(d)
    return out


def _static_pool(region: str) -> list[dict]:
    """Last-resort pool: the pre-built static JSON (already camelCase)."""
    path = _static_pool_path(region)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class ProfileIn(BaseModel):
    userId: str = Field(..., description="anonymous uuid from the client")
    mbti: str = ""
    zodiac: str = ""
    preferences: list[str] = Field(default_factory=list)
    region: str = DEFAULT_REGION


class DrawIn(BaseModel):
    userId: Optional[str] = None
    id: str = Field(..., description="the drawn POI's id (amap_id)")
    luck: int = 0
    isSuper: bool = False
    region: str = DEFAULT_REGION


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health(region: str = Query(DEFAULT_REGION)):
    conn = _open(region)
    if conn is None:
        return {"ok": False, "region": region, "reason": "db not found",
                "staticFallback": _static_pool_path(region).exists()}
    try:
        counts = db.count_pois(conn)
        has_copy = conn.execute("SELECT COUNT(*) FROM poi_copy").fetchone()[0]
        has_pool = conn.execute(
            "SELECT COUNT(*) FROM recommend_pool"
        ).fetchone()[0]
        return {
            "ok": True,
            "region": region,
            "poi": counts,
            "poiCopy": has_copy,
            "recommendPool": has_pool,
        }
    finally:
        conn.close()


@app.get("/api/pool")
def pool(
    region: str = Query(DEFAULT_REGION),
    mbti: str = Query(""),
    preferences: str = Query("", description="comma-separated preference ids"),
):
    """
    Return the suggestion pool for a profile, camelCase, ready for gacha.ts.

    Fallback chain (see module docstring): recommend_pool -> live-weighted copy
    -> static JSON. `source` in the response says which path served it.
    """
    prefs = [p for p in preferences.split(",") if p]
    labels = _category_labels(region)
    conn = _open(region)

    if conn is not None:
        try:
            # 1) offline-built, profile-weighted pool
            key = recommend.profile_key(mbti, prefs)
            rows = _pool_from_recommend(conn, key)
            if not rows and key != "default":
                rows = _pool_from_recommend(conn, "default")
            if rows:
                items = [_to_suggestion(r, labels) for r in rows]
                return {"region": region, "source": "recommend_pool",
                        "profileKey": key, "count": len(items), "items": items}

            # 2) live-weighted, from all POIs that have copy
            copy_rows = _rows_with_copy(conn)
            if copy_rows:
                heat = recommend._area_heat(conn)
                for r in copy_rows:
                    r["area_heat"] = heat.get(r.get("business_area") or "", None)
                copy_rows.sort(
                    key=lambda r: recommend.weight_for(r, mbti, prefs),
                    reverse=True,
                )
                items = [_to_suggestion(r, labels) for r in copy_rows]
                return {"region": region, "source": "live_weighted",
                        "count": len(items), "items": items}
        finally:
            conn.close()

    # 3) static JSON (already camelCase)
    items = _static_pool(region)
    return {"region": region, "source": "static", "count": len(items),
            "items": items}


@app.post("/api/profile")
def save_profile(body: ProfileIn):
    conn = _open(body.region)
    if conn is None:
        # No DB for this region yet — accept the write as a no-op so the client
        # never sees an error; the profile still lives in the client's storage.
        return {"ok": True, "persisted": False, "region": body.region}
    try:
        db.init_db(conn)
        db.upsert_profile(conn, body.userId, body.mbti, body.zodiac,
                          body.preferences)
        return {"ok": True, "persisted": True, "region": body.region}
    finally:
        conn.close()


@app.post("/api/draw")
def record_draw(body: DrawIn):
    conn = _open(body.region)
    if conn is None:
        return {"ok": True, "persisted": False, "region": body.region}
    try:
        db.init_db(conn)
        db.record_draw(conn, body.userId, body.id, body.luck, body.isSuper)
        return {"ok": True, "persisted": True, "region": body.region}
    finally:
        conn.close()
