"""
Step 2 - Clean and enrich the raw POI skeletons.

The product's promise is narrow: every suggestion must be startable *now*,
finishable in 30min-5h, and nearby. This stage enforces that boundary, because
no amount of good copy can rescue a suggestion for a car dealership.

Usage:
    python3 pipeline/clean.py
"""

from __future__ import annotations

import json
import math
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import (  # noqa: E402
    CATEGORIES,
    CHAIN_NAME_PATTERNS,
    CLEAN_POI_FILE,
    DIRECTIONS,
    DISTRICT_BBOX,
    HEAVY_NAME_PATTERNS,
    RARITY_WEIGHTS,
    RAW_POI_FILE,
)

# 朝阳区 centroid, used for the 玄学 direction field.
CENTER_LNG = (DISTRICT_BBOX[0] + DISTRICT_BBOX[2]) / 2
CENTER_LAT = (DISTRICT_BBOX[1] + DISTRICT_BBOX[3]) / 2


def is_heavy(name: str, amap_type: str) -> bool:
    blob = f"{name} {amap_type}"
    return any(pattern in blob for pattern in HEAVY_NAME_PATTERNS)


def is_chain(name: str) -> bool:
    return any(pattern in name for pattern in CHAIN_NAME_PATTERNS)


# Branch suffixes we strip from a display name: "(三里屯店)", "望京店", "东坝店3",
# "NO.2" ... These carry no meaning for the copy — the user just needs the
# brand. We keep the original `name` for dedup/matching and only trim the
# customer-facing display string.
_BRANCH_SUFFIX = re.compile(
    r"[（(][^（()）]*店[^（()）]*[)）]\s*\d*$"   # (望京店) / (三里屯旗舰店)
    r"|[·\-—\s][^·\-—\s]{0,8}店\s*\d*$"          # ·东坝店3 / -望京店
    r"|\d+\s*号店$"                               # 12号店
)


def display_name(name: str) -> str:
    """
    The brand-only name shown to the user.

    Rule: keep the part before the first "·" separator when what follows looks
    like a branch tag (contains 店). Then strip any trailing branch suffix. Fall
    back to the original whenever trimming would leave too little to be a name,
    so real names that merely contain "·" (e.g. "Blue Bottle·三里屯") or short
    names are never mangled.
    """
    original = (name or "").strip()
    if not original:
        return original

    candidate = original
    # If there's a "·" and the tail mentions a branch (店), take the head.
    if "·" in candidate:
        head, tail = candidate.split("·", 1)
        head = head.strip()
        if "店" in tail and len(head) >= 2:
            candidate = head

    # Strip a trailing branch suffix if one remains (handles no-"·" cases too).
    trimmed = _BRANCH_SUFFIX.sub("", candidate).strip()
    if len(trimmed) >= 2:
        candidate = trimmed

    return candidate if len(candidate) >= 2 else original


def parse_open_hours(text: str, fallback: tuple[int, int]) -> tuple[int, int]:
    """
    Extract an (open_hour, close_hour) pair from AMap's free-text opening hours.

    AMap is wildly inconsistent here: "09:00-22:00", "周一至周日 10:00-21:00",
    "24小时营业" all appear. We only need coarse hours to decide whether a place
    is plausibly open, so a regex over the first time range is enough.
    """
    if not text:
        return fallback
    if "24" in text and "小时" in text:
        return (0, 24)
    match = re.search(r"(\d{1,2})[:：](\d{2})\s*[-~至]\s*(\d{1,2})[:：](\d{2})", text)
    if not match:
        return fallback
    start = int(match.group(1))
    end = int(match.group(3))
    # Places closing after midnight report e.g. 18:00-02:00.
    if end <= start:
        end += 24
    if not (0 <= start <= 24):
        return fallback
    return (start, min(end, 30))


def infer_duration(category: str, name: str) -> int:
    """Pick a plausible dwell time inside the category's range."""
    low, high = CATEGORIES[category]["duration_minutes"]
    # Bigger venues skew longer; a rough but useful signal.
    if any(k in name for k in ("博物馆", "美术馆", "公园", "森林", "遗址")):
        low = int(low + (high - low) * 0.4)
    return random.randint(low, high)


def bearing_direction(lng: float, lat: float) -> str:
    """Compass direction of the POI relative to the district centre."""
    dx = lng - CENTER_LNG
    dy = lat - CENTER_LAT
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return random.choice(DIRECTIONS)
    angle = math.degrees(math.atan2(dx, dy)) % 360
    index = int((angle + 22.5) // 45) % 8
    # DIRECTIONS is ordered E, SE, S, SW, W, NW, N, NE starting from north.
    order = ["正北", "东北", "正东", "东南", "正南", "西南", "正西", "西北"]
    return order[index]


def pick_rarity(poi: dict) -> str:
    """
    Assign a rarity tier.

    Rarity should feel earned, so quality signals raise a POI's chances. But
    the overall distribution must still track RARITY_WEIGHTS, otherwise "rare"
    stops feeling rare and the gacha loses its ceremony.

    We therefore apply the bonus as a *multiplier* on the rare/uncommon
    probabilities (capped), rather than shifting the roll, which previously
    inflated rare to ~33%.
    """
    bonus = 1.0
    rating = poi.get("rating")
    try:
        if rating and float(rating) >= 4.5:
            bonus += 0.5
    except (TypeError, ValueError):
        pass
    if poi.get("photo_count", 0) >= 5:
        bonus += 0.3
    if poi.get("category") in ("weird", "culture"):
        bonus += 0.4
    # Cap so a single POI can be at most ~2x baseline odds.
    bonus = min(bonus, 2.0)

    rare_p = min(RARITY_WEIGHTS["rare"] * bonus, 0.20)
    uncommon_p = min(RARITY_WEIGHTS["uncommon"] * bonus, 0.40)

    roll = random.random()
    if roll < rare_p:
        return "rare"
    if roll < rare_p + uncommon_p:
        return "uncommon"
    return "common"


def lucky_value(rarity: str) -> int:
    """Lucky score shown on the badge. Rarer capsules skew higher."""
    if rarity == "rare":
        return random.randint(80, 99)
    if rarity == "uncommon":
        return random.randint(50, 85)
    return random.randint(10, 70)


def main() -> int:
    raw_path = Path(RAW_POI_FILE)
    if not raw_path.exists():
        print(f"ERROR: {raw_path} not found. Run collect.py first.", file=sys.stderr)
        return 1

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(raw)} raw POIs")

    seen_names: set[str] = set()
    clean: list[dict] = []
    dropped = {"heavy": 0, "chain": 0, "nameless": 0, "dup": 0, "nogeo": 0}

    for poi in raw:
        name = (poi.get("name") or "").strip()
        if not name or len(name) < 2:
            dropped["nameless"] += 1
            continue
        if poi.get("lng") is None or poi.get("lat") is None:
            dropped["nogeo"] += 1
            continue
        if is_heavy(name, poi.get("amap_type", "")):
            dropped["heavy"] += 1
            continue
        if is_chain(name):
            dropped["chain"] += 1
            continue
        # Same brand at different branches produces near-identical copy.
        norm = re.sub(r"[（(].*?[)）]|\s+", "", name)
        if norm in seen_names:
            dropped["dup"] += 1
            continue
        seen_names.add(norm)

        category = poi["category"]
        open_h, close_h = parse_open_hours(
            poi.get("opentime", ""), CATEGORIES[category]["default_open"]
        )
        rarity = pick_rarity(poi)
        clean.append({
            **poi,
            # Keep the raw `name` for matching; add a brand-only display name.
            "display_name": display_name(name),
            "open_hour": open_h,
            "close_hour": close_h,
            "duration_minutes": infer_duration(category, name),
            "direction": bearing_direction(poi["lng"], poi["lat"]),
            "rarity": rarity,
            "lucky": lucky_value(rarity),
        })

    out = Path(CLEAN_POI_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Kept {len(clean)}  |  dropped {dropped}")
    by_cat: dict[str, int] = {}
    for item in clean:
        by_cat[item["category"]] = by_cat.get(item["category"], 0) + 1
    for key, count in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"  {key:8s} {count}")
    print(f"Saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
