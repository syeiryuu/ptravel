"""
Data-access layer for the self-owned POI database (SQLite).

Why this module exists
----------------------
AMap's free tier caps how many POIs we can fetch, and every fetch costs quota.
So we treat AMap as a *seed*, not a live dependency: everything we ever fetch is
upserted into our own `poi` table and reused forever. clean / copy / recommend
all read from this one store instead of re-hitting AMap.

Design notes
------------
* One tiny wrapper around sqlite3; no ORM. The schema is small and the queries
  are simple, so an ORM would add more concepts than it removes.
* `upsert_poi` is idempotent and keyed on amap_id: re-collecting the same POI
  refreshes its fields and bumps `last_seen_at`, but never duplicates a row and
  never loses `first_seen_at`.
* JSON-shaped columns (photo_titles, sources, preferences) are stored as JSON
  text and (de)serialised at this boundary, so callers work with real lists.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import DB_FILE, DB_SCHEMA_FILE

# Columns that live as JSON text in SQLite but as Python lists in memory.
_JSON_COLUMNS = {"photo_titles", "sources", "preferences"}


def now_iso() -> str:
    """Current UTC time as an ISO8601 string (schema stores all times this way)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _project_root() -> Path:
    # pipeline/db/database.py -> repo root is two levels up.
    return Path(__file__).resolve().parent.parent.parent


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection with sensible defaults and rows as dict-like objects."""
    path = Path(db_path) if db_path else _project_root() / DB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # better concurrent reads
    return conn


def init_db(conn: sqlite3.Connection | None = None,
            schema_path: str | None = None) -> sqlite3.Connection:
    """Create tables if they do not exist. Safe to call repeatedly."""
    own = conn is None
    conn = conn or connect()
    path = Path(schema_path) if schema_path else _project_root() / DB_SCHEMA_FILE
    conn.executescript(path.read_text(encoding="utf-8"))
    conn.commit()
    if own:
        # Leave the connection open for the caller when they passed one in,
        # otherwise it is theirs to manage — so we return it either way.
        pass
    return conn


# --- Row (de)serialisation -------------------------------------------------

def _dump_json_fields(data: dict) -> dict:
    out = dict(data)
    for col in _JSON_COLUMNS:
        if col in out and not isinstance(out[col], str):
            out[col] = json.dumps(out[col] if out[col] is not None else [],
                                  ensure_ascii=False)
    return out


def _load_json_fields(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    for col in _JSON_COLUMNS:
        if col in data and isinstance(data[col], str):
            try:
                data[col] = json.loads(data[col])
            except (json.JSONDecodeError, TypeError):
                data[col] = []
    return data


# --- POI upsert ------------------------------------------------------------

# Fields we persist for a POI, aligned with collect._normalise() output.
_POI_FIELDS = [
    "amap_id", "name", "category", "amap_type", "typecode", "address",
    "adname", "business_area", "lng", "lat", "tel", "opentime", "rating",
    "cost", "tag", "alias", "photo_titles", "photo_count", "source",
]


def upsert_poi(conn: sqlite3.Connection, poi: dict,
               source: str = "amap") -> bool:
    """
    Insert or update one POI, keyed on amap_id.

    Accepts either the raw AMap shape (id/photo_titles/...) already normalised
    by collect._normalise, or a dict that uses `amap_id` directly.

    Returns True when the POI is new (first time we have ever seen it), so
    callers can report how much fresh data a collection run actually produced.
    """
    amap_id = poi.get("amap_id") or poi.get("id")
    if not amap_id:
        raise ValueError("POI has no id / amap_id")

    now = now_iso()
    values = {
        "amap_id": amap_id,
        "name": poi.get("name", ""),
        "category": poi.get("category", ""),
        "amap_type": poi.get("amap_type", ""),
        "typecode": poi.get("typecode", ""),
        "address": poi.get("address", ""),
        "adname": poi.get("adname", ""),
        "business_area": poi.get("business_area", ""),
        "lng": poi.get("lng"),
        "lat": poi.get("lat"),
        "tel": poi.get("tel", ""),
        "opentime": poi.get("opentime", ""),
        "rating": poi.get("rating", ""),
        "cost": poi.get("cost", ""),
        "tag": poi.get("tag", ""),
        "alias": poi.get("alias", ""),
        "photo_titles": poi.get("photo_titles", []),
        "photo_count": poi.get("photo_count", 0),
        "source": source,
    }
    values = _dump_json_fields(values)

    existing = conn.execute(
        "SELECT amap_id FROM poi WHERE amap_id = ?", (amap_id,)
    ).fetchone()
    is_new = existing is None

    if is_new:
        cols = _POI_FIELDS + ["first_seen_at", "last_seen_at"]
        placeholders = ", ".join(["?"] * len(cols))
        params = [values[f] for f in _POI_FIELDS] + [now, now]
        conn.execute(
            f"INSERT INTO poi ({', '.join(cols)}) VALUES ({placeholders})",
            params,
        )
    else:
        # Refresh mutable fact fields + last_seen_at; keep first_seen_at intact.
        #
        # Crucially, an *empty* incoming value must NOT wipe a non-empty stored
        # one. Different sources carry different subsets of fields (raw_poi has
        # rating/cost/tag; the trimmed gacha.json does not), so a later upsert
        # from a leaner source would otherwise blank out good data. We therefore
        # only overwrite a column when the new value is actually present.
        update_fields = [f for f in _POI_FIELDS if f != "amap_id"]
        assignments = []
        params: list = []
        for field in update_fields:
            new_value = values[field]
            is_empty = new_value is None or (isinstance(new_value, str)
                                             and new_value.strip() == "")
            if is_empty:
                # Keep the existing value; only replace if it too was empty/null.
                assignments.append(
                    f"{field} = CASE WHEN {field} IS NULL OR {field} = '' "
                    f"THEN ? ELSE {field} END"
                )
            else:
                assignments.append(f"{field} = ?")
            params.append(new_value)
        params += [now, amap_id]
        conn.execute(
            f"UPDATE poi SET {', '.join(assignments)}, last_seen_at = ? "
            f"WHERE amap_id = ?",
            params,
        )
    return is_new


def upsert_pois(conn: sqlite3.Connection, pois: list[dict],
                source: str = "amap") -> tuple[int, int]:
    """Bulk upsert. Returns (new_count, total_count)."""
    new_count = 0
    for poi in pois:
        if upsert_poi(conn, poi, source=source):
            new_count += 1
    conn.commit()
    return new_count, len(pois)


# --- POI copy (clean + generate products) ----------------------------------

_COPY_FIELDS = [
    "amap_id", "open_hour", "close_hour", "duration_minutes", "direction",
    "rarity", "lucky", "hook", "reason", "oracle", "action", "sources",
    "copy_version", "generated_at",
]


def upsert_poi_copy(conn: sqlite3.Connection, copy: dict,
                    copy_version: int = 1) -> None:
    """Insert or replace the derived copy for one POI."""
    amap_id = copy.get("amap_id") or copy.get("id")
    if not amap_id:
        raise ValueError("copy has no id / amap_id")
    values = {
        "amap_id": amap_id,
        "open_hour": copy.get("open_hour"),
        "close_hour": copy.get("close_hour"),
        "duration_minutes": copy.get("duration_minutes"),
        "direction": copy.get("direction"),
        "rarity": copy.get("rarity", "common"),
        "lucky": copy.get("lucky", 50),
        "hook": copy.get("hook", ""),
        "reason": copy.get("reason", ""),
        "oracle": copy.get("oracle", ""),
        "action": copy.get("action", ""),
        "sources": copy.get("sources", []),
        "copy_version": copy_version,
        "generated_at": now_iso(),
    }
    values = _dump_json_fields(values)
    placeholders = ", ".join(["?"] * len(_COPY_FIELDS))
    conn.execute(
        f"INSERT OR REPLACE INTO poi_copy ({', '.join(_COPY_FIELDS)}) "
        f"VALUES ({placeholders})",
        [values[f] for f in _COPY_FIELDS],
    )
    conn.commit()


# --- Queries ---------------------------------------------------------------

def get_poi(conn: sqlite3.Connection, amap_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM poi WHERE amap_id = ?", (amap_id,)).fetchone()
    return _load_json_fields(row)


def iter_pois(conn: sqlite3.Connection, category: str | None = None):
    """Yield POIs (optionally filtered by category) as dicts."""
    if category:
        cur = conn.execute("SELECT * FROM poi WHERE category = ?", (category,))
    else:
        cur = conn.execute("SELECT * FROM poi")
    for row in cur:
        yield _load_json_fields(row)


def get_poi_with_copy(conn: sqlite3.Connection, amap_id: str) -> dict | None:
    """A POI joined with its derived copy — the app-facing shape."""
    row = conn.execute(
        "SELECT p.*, c.open_hour, c.close_hour, c.duration_minutes, "
        "c.direction, c.rarity, c.lucky, c.hook, c.reason, c.oracle, "
        "c.action, c.sources "
        "FROM poi p LEFT JOIN poi_copy c ON p.amap_id = c.amap_id "
        "WHERE p.amap_id = ?",
        (amap_id,),
    ).fetchone()
    return _load_json_fields(row)


def count_pois(conn: sqlite3.Connection) -> dict[str, int]:
    """Per-category POI counts, plus a 'total'."""
    rows = conn.execute(
        "SELECT category, COUNT(*) AS n FROM poi GROUP BY category"
    ).fetchall()
    result = {r["category"]: r["n"] for r in rows}
    result["total"] = sum(result.values())
    return result


# --- User profile ----------------------------------------------------------

def upsert_profile(conn: sqlite3.Connection, user_id: str, mbti: str = "",
                   zodiac: str = "", preferences: list[str] | None = None) -> None:
    now = now_iso()
    prefs = json.dumps(preferences or [], ensure_ascii=False)
    conn.execute(
        "INSERT INTO user_profile (user_id, mbti, zodiac, preferences, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET mbti = excluded.mbti, "
        "zodiac = excluded.zodiac, preferences = excluded.preferences, "
        "updated_at = excluded.updated_at",
        (user_id, mbti, zodiac, prefs, now, now),
    )
    conn.commit()


def get_profile(conn: sqlite3.Connection, user_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM user_profile WHERE user_id = ?", (user_id,)
    ).fetchone()
    data = dict(row) if row else None
    if data and isinstance(data.get("preferences"), str):
        try:
            data["preferences"] = json.loads(data["preferences"])
        except json.JSONDecodeError:
            data["preferences"] = []
    return data


# --- Collect log -----------------------------------------------------------

def log_collect(conn: sqlite3.Connection, category: str, grid_cell: str,
                found_count: int, new_count: int) -> None:
    conn.execute(
        "INSERT INTO collect_log (category, grid_cell, found_count, "
        "new_count, collected_at) VALUES (?, ?, ?, ?, ?)",
        (category, grid_cell, found_count, new_count, now_iso()),
    )
    conn.commit()


# --- Draw history ----------------------------------------------------------

def record_draw(conn: sqlite3.Connection, user_id: str | None, amap_id: str,
                luck: int, is_super: bool) -> None:
    conn.execute(
        "INSERT INTO draw_history (user_id, amap_id, luck, is_super, drawn_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, amap_id, luck, 1 if is_super else 0, now_iso()),
    )
    conn.commit()


if __name__ == "__main__":
    # `python3 -m pipeline.db.database` initialises an empty database.
    c = connect()
    init_db(c)
    print("Initialised DB. POI counts:", count_pois(c))
