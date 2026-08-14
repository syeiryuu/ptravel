"""
T+1 offline recommendation builder.

What it does
------------
For every distinct *user profile* we compute a weighted candidate pool and cache
it in `recommend_pool`. The app then just reads the pool for a user's profile
instead of scoring POIs live — the "T+1 离线预生成" the product asked for.

The weighting rule (the product's ask)
---------------------------------------
We do NOT invent new tags. We reuse the fields we already have — from AMap
(`category`, `rating`, `cost`, `business_area`, opening hours) plus the user's
own profile (MBTI / 星座 / 今日偏好) — and nudge weights up or down.

MBTI is read as its four independent axes, and each axis is inferred onto a
*different* real field, so all 16 types get a genuinely distinct pool rather
than a coarse "introvert likes cafes":

    E/I  外向找人气 / 内向求独处   -> category + business_area 商圈热度
    S/N  实感重体验 / 直觉爱新奇   -> category + rating (确定性 vs 想象空间)
    T/F  思考求价值 / 情感重氛围   -> cost band + category
    J/P  判断要计划 / 知觉爱随性   -> close_hour + duration (规律 vs 灵活/深夜)

Everything is a multiplier on a base weight, so the pool stays diverse: a place
is nudged, never hard-filtered (except venues with no usable copy). Fields that
are missing on a POI simply contribute no nudge — the rule degrades silently.

Profile key
-----------
Users with the same *relevant* profile share one precomputed pool. The key is a
normalised fingerprint like "mbti=INFP;pref=forage,idle". The full 4-letter MBTI
is used now that every axis moves the weights (zodiac is still left out: its
effect is tiny and would multiply the bucket count). This keeps the number of
pools bounded while giving each personality a distinct pool.

Usage:
    python3 -m pipeline.db.recommend                 # build for all seen profiles
    python3 -m pipeline.db.recommend --demo          # build a few sample profiles
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pipeline.db import database as db  # noqa: E402

# --- Preference -> product category affinity -------------------------------
# Maps each 今日偏好 pill (see App.tsx PREFERENCES) to the categories it favours.
#
# Each pill lists categories from *both* regions. The keys are disjoint (Beijing
# uses cafe/food/park/... ; the Dolomites uses hut/peak/lake/... ; only "food"
# overlaps, and it means "eat" in both), so one map serves both places and a
# category simply never fires on the region that lacks it — the rule degrades
# silently, exactly like the missing rating/cost fields do.
PREFERENCE_CATEGORIES: dict[str, tuple[str, ...]] = {
    # 觅食: eat. Beijing 餐馆 / 多洛米蒂 山谷餐厅 + 半山小屋（也管饭）
    "forage": ("food", "hut"),
    # 流汗: move. Beijing 公园 / 多洛米蒂 徒步·登顶
    "sweat": ("park", "trail", "peak"),
    # 溜达: wander. Beijing 公园·逛街 / 多洛米蒂 山城·湖边
    "stroll": ("park", "shop", "village", "lake"),
    # 放空: idle. Beijing 咖啡·公园 / 多洛米蒂 看湖·看山·坐缆车发呆
    "idle": ("cafe", "park", "lake", "peak", "cable"),
    "fate": (),                             # 随缘 -> no nudge, pure luck
}

# --- MBTI: four axes, each inferred onto a different real field -------------
# Each axis contributes an independent, gentle multiplier. A place that a type
# agrees with on several axes compounds those nudges, so e.g. INFP and ESTJ end
# up with visibly different pools. All rules use only existing fields, and any
# field that is missing on a POI simply yields no nudge (rule degrades).

# E/I  ->  which categories, reinforced by business-area busyness.
# (Beijing keys first, Dolomites keys appended; see PREFERENCE_CATEGORIES note.)
MBTI_EI_CATEGORIES: dict[str, tuple[str, ...]] = {
    # 外向：热闹、有人气  -> 山城/缆车站/餐厅（人聚的地方）
    "E": ("night", "food", "shop", "village", "cable"),
    # 内向：安静、可独处  -> 山顶/湖边/徒步（人少的地方）
    "I": ("cafe", "culture", "park", "peak", "lake", "trail"),
}
# S/N  ->  concrete-and-proven vs. novel-and-imaginative.
MBTI_SN_CATEGORIES: dict[str, tuple[str, ...]] = {
    # 实感：具体、可体验  -> 餐厅/山城/小屋（吃得到、走得到）
    "S": ("food", "shop", "park", "village", "hut"),
    # 直觉：小众、有想象空间 -> 山顶/徒步/高山湖（风景与想象）
    "N": ("weird", "culture", "night", "peak", "trail", "lake"),
}
# T/F  ->  categories where the axis's motivation is best served.
MBTI_TF_CATEGORIES: dict[str, tuple[str, ...]] = {
    # 思考：有信息量、值得琢磨 -> 山城（人文）/徒步（路书感）
    "T": ("culture", "weird", "village", "trail"),
    # 情感：氛围、待着舒服   -> 小屋/餐厅/湖边（暖、慢、舒服）
    "F": ("cafe", "food", "park", "hut", "lake"),
}
# J/P is inferred from opening hours + dwell time rather than category:
#   J 判断型 -> 规律、白天打烊、时长明确  (close_hour <= 21)
#   P 知觉型 -> 灵活、深夜还开、随性       (close_hour >= 22)

# Multipliers. Each nudge is small; up to four axes may compound, so the cap
# below keeps a fully-aligned POI from running away with the pool.
PREFERENCE_BOOST = 1.5      # a chosen preference favours its categories
MBTI_AXIS_BOOST = 1.18      # per-axis category agreement (E/I, S/N, T/F)
MBTI_SN_RATING_BOOST = 1.12 # S + high rating (实感型偏好确定性)
MBTI_TF_COST_BOOST = 1.12   # T + cheap  /  F + mid-priced
MBTI_JP_HOURS_BOOST = 1.15  # J + early-close  /  P + late-close
MBTI_MAX = 1.9              # cap on the combined MBTI multiplier
RATING_BOOST = 1.15         # AMap rating >= 4.5 (profile-independent quality)
RATING_HIGH = 4.5


def _num(value) -> float | None:
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def mbti_multiplier(poi: dict, mbti: str) -> float:
    """
    Combined MBTI nudge for one POI, from all four axes, using real fields.

    `poi` is a joined poi+poi_copy row: category, rating, cost, close_hour,
    duration_minutes, area_heat (see _joined_pois). Returns a multiplier in
    roughly [0.85, MBTI_MAX]; a type may also mildly *down*-weight places that
    contradict it (e.g. an introvert in the busiest area), which sharpens the
    difference between types without ever hard-filtering.
    """
    if not mbti or len(mbti) < 4:
        return 1.0
    m = mbti.upper()
    ei = "E" if "E" in m else ("I" if "I" in m else "")
    sn = "S" if "S" in m else ("N" if "N" in m else "")
    tf = "T" if "T" in m else ("F" if "F" in m else "")
    jp = "J" if "J" in m else ("P" if "P" in m else "")

    category = poi.get("category", "")
    mult = 1.0

    # --- E/I: category + business-area busyness ---------------------------
    if ei and category in MBTI_EI_CATEGORIES.get(ei, ()):
        mult *= MBTI_AXIS_BOOST
    heat = poi.get("area_heat")  # 0..1, share-of-max POI density in that 商圈
    if ei and heat is not None:
        if ei == "E" and heat >= 0.66:
            mult *= 1.12            # extrovert leans into the busiest areas
        elif ei == "I" and heat <= 0.33:
            mult *= 1.12            # introvert leans into the quiet ones
        elif ei == "E" and heat <= 0.33:
            mult *= 0.9             # ...and mildly away from the wrong end
        elif ei == "I" and heat >= 0.66:
            mult *= 0.9

    # --- S/N: category + rating (certainty vs. imagination) ---------------
    if sn and category in MBTI_SN_CATEGORIES.get(sn, ()):
        mult *= MBTI_AXIS_BOOST
    rating = _num(poi.get("rating"))
    if sn == "S" and rating is not None and rating >= 4.3:
        mult *= MBTI_SN_RATING_BOOST  # sensors trust the proven pick

    # --- T/F: cost band + category ----------------------------------------
    if tf and category in MBTI_TF_CATEGORIES.get(tf, ()):
        mult *= MBTI_AXIS_BOOST
    cost = _num(poi.get("cost"))
    if cost is not None:
        if tf == "T" and cost <= 60:
            mult *= MBTI_TF_COST_BOOST   # thinkers like a defensible-value pick
        elif tf == "F" and 60 < cost <= 200:
            mult *= MBTI_TF_COST_BOOST   # feelers pay for the right atmosphere

    # --- J/P: opening hours + dwell time ----------------------------------
    close_h = poi.get("close_hour")
    duration = poi.get("duration_minutes")
    if jp and close_h is not None:
        if jp == "P" and close_h >= 22:
            mult *= MBTI_JP_HOURS_BOOST  # perceivers keep late, flexible options
        elif jp == "J" and close_h <= 21:
            mult *= MBTI_JP_HOURS_BOOST  # judgers prefer the predictable day plan
    if jp == "J" and duration is not None and duration <= 90:
        mult *= 1.06                     # judgers like a clean, bounded outing

    return min(mult, MBTI_MAX)


def profile_key(mbti: str, preferences: list[str]) -> str:
    """
    Normalised fingerprint used to bucket users who should share a pool.

    Uses only the MBTI I/E axis and the sorted preference set — the two things
    that actually move the weights meaningfully. Anonymous/empty profiles map
    to the key "default".
    """
    parts = []
    if mbti and len(mbti) == 4:
        parts.append(f"mbti={mbti.upper()}")
    prefs = sorted(p for p in (preferences or []) if p and p != "fate")
    if prefs:
        parts.append("pref=" + ",".join(prefs))
    return ";".join(parts) if parts else "default"


def weight_for(poi_copy: dict, mbti: str, preferences: list[str]) -> float:
    """
    Compute a POI's weight for a given profile using only existing fields.

    `poi_copy` is a joined poi + poi_copy row (category, rating, cost,
    close_hour, duration_minutes, rarity, area_heat).
    """
    category = poi_copy.get("category", "")
    weight = 1.0

    # 1) rarity damping — keep rare capsules genuinely rare (mirrors gacha.ts).
    rarity = poi_copy.get("rarity", "common")
    if rarity == "rare":
        weight *= 0.45
    elif rarity == "uncommon":
        weight *= 0.75

    # 2) preference nudge (the strongest signal — it's an explicit choice).
    favoured: set[str] = set()
    for pref in preferences or []:
        favoured.update(PREFERENCE_CATEGORIES.get(pref, ()))
    if category in favoured:
        weight *= PREFERENCE_BOOST

    # 3) MBTI nudge — all four axes inferred onto real fields (see above).
    weight *= mbti_multiplier(poi_copy, mbti)

    # 4) rating nudge — profile-independent quality signal. Never exposes the
    #    number, only uses it. (S-types get an extra rating nudge inside mbti_*.)
    if (r := _num(poi_copy.get("rating"))) is not None and r >= RATING_HIGH:
        weight *= RATING_BOOST

    return round(weight, 4)


def _area_heat(conn) -> dict[str, float]:
    """
    Busyness of each 商圈, as a 0..1 share of the densest one.

    We have no crowd data, but the number of POIs we hold in a business_area is
    a fair proxy for how lively it is — and it is a real, checkable count, not a
    guess. Used by the E/I axis (extroverts lean busy, introverts lean quiet).
    """
    rows = conn.execute(
        "SELECT business_area AS area, COUNT(*) AS n FROM poi "
        "WHERE business_area != '' GROUP BY business_area"
    ).fetchall()
    if not rows:
        return {}
    max_n = max(r["n"] for r in rows) or 1
    return {r["area"]: r["n"] / max_n for r in rows}


def _joined_pois(conn) -> list[dict]:
    """POIs that have usable copy (a hook), joined with the fields we weight on."""
    heat = _area_heat(conn)
    rows = conn.execute(
        "SELECT p.amap_id, p.category, p.rating, p.cost, p.business_area, "
        "c.rarity, c.close_hour, c.duration_minutes, c.hook "
        "FROM poi p JOIN poi_copy c ON p.amap_id = c.amap_id "
        "WHERE c.hook IS NOT NULL AND c.hook != ''"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["area_heat"] = heat.get(d.get("business_area") or "", None)
        result.append(d)
    return result


def build_pool(conn, mbti: str, preferences: list[str],
               top_n: int | None = None) -> int:
    """
    Build (or rebuild) the recommend_pool for one profile. Returns row count.

    The previous batch for this profile_key is deleted first, so the pool is
    always a clean T+1 snapshot rather than an ever-growing pile.
    """
    key = profile_key(mbti, preferences)
    pois = _joined_pois(conn)
    # A stable per-POI jitter breaks ties so equally-weighted POIs of the same
    # category don't clump at the top of the ranking; it does not change the
    # weight used for the weighted draw, only the stored rank ordering.
    import hashlib

    def _jitter(amap_id: str) -> float:
        h = hashlib.md5(amap_id.encode("utf-8")).hexdigest()
        return int(h[:8], 16) / 0xFFFFFFFF  # 0..1, deterministic

    scored = [
        (p["amap_id"], weight_for(p, mbti, preferences))
        for p in pois
    ]
    # Sort by weight, then by deterministic jitter so the top of the pool is a
    # varied mix rather than a single category block.
    scored.sort(key=lambda kv: (kv[1], _jitter(kv[0])), reverse=True)
    if top_n:
        scored = scored[:top_n]

    built_at = db.now_iso()
    conn.execute("DELETE FROM recommend_pool WHERE profile_key = ?", (key,))
    conn.executemany(
        "INSERT INTO recommend_pool (profile_key, amap_id, weight, rank, "
        "built_at) VALUES (?, ?, ?, ?, ?)",
        [(key, aid, w, rank, built_at)
         for rank, (aid, w) in enumerate(scored, start=1)],
    )
    conn.commit()
    return len(scored)


def build_all(conn, top_n: int | None = None) -> None:
    """
    Rebuild pools for every profile we have actually seen, plus 'default'.

    This is the nightly T+1 job: it only computes pools that at least one real
    user needs, so cost scales with your user base, not with the combinatorial
    space of all possible profiles.
    """
    profiles = conn.execute(
        "SELECT DISTINCT mbti, preferences FROM user_profile"
    ).fetchall()

    seen_keys: set[str] = set()
    # Always maintain a default pool for anonymous / empty profiles.
    n = build_pool(conn, "", [], top_n=top_n)
    seen_keys.add("default")
    print(f"  [default] {n} POIs")

    import json as _json
    for row in profiles:
        mbti = row["mbti"] or ""
        try:
            prefs = _json.loads(row["preferences"] or "[]")
        except _json.JSONDecodeError:
            prefs = []
        key = profile_key(mbti, prefs)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        n = build_pool(conn, mbti, prefs, top_n=top_n)
        print(f"  [{key}] {n} POIs")

    print(f"Built {len(seen_keys)} profile pool(s).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build T+1 recommendation pools")
    parser.add_argument("--top-n", type=int, default=None,
                        help="keep only the top N weighted POIs per pool")
    parser.add_argument("--demo", action="store_true",
                        help="build a few illustrative sample profiles")
    args = parser.parse_args()

    conn = db.connect()
    db.init_db(conn)

    if args.demo:
        samples = [
            ("INFP", ["idle"]),
            ("ENTJ", ["forage"]),
            ("ISTJ", ["stroll", "sweat"]),
            ("", []),
        ]
        for mbti, prefs in samples:
            n = build_pool(conn, mbti, prefs, top_n=args.top_n)
            print(f"  [{profile_key(mbti, prefs)}] {n} POIs "
                  f"(mbti={mbti or '-'}, prefs={prefs or '-'})")
    else:
        build_all(conn, top_n=args.top_n)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
