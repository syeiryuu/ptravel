"""
Offline copy generator for the 多洛米蒂 (Dolomites) build.

Why a separate generator: pipeline/local_generate.py is tuned for the Beijing
city buckets (深夜食堂 / 胡同 / 评分 / 菜品) and its templates almost never fire on
mountain signals, so it skips ~95% of Alpine POIs. This module instead speaks
the mountain vocabulary — huts, peaks, lakes, cable cars, elevation, cuisine —
and weaves the *real* signals into copy the way the prompt asks the LLM to.

It follows the exact same contract as generate.py:
  * facts come only from build_signals(poi)      -> 虚实相应
  * the copy is addressed to a specific persona   -> 懂你
  * a light, non-mystical touch of luck           -> 浅浅好运感
  * output passes validate_copy()                 -> product voice rules

This is for *seeing the effect* without an API key. When a key is available,
prefer generate.py, which produces far more varied phrasing.

Usage:
    PTRAVEL_REGION=dolomites python3 pipeline/local_generate_dolomites.py \
        [--limit 60] [--mbti ENFP] [--zodiac 射手座] [--pref sweat]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import CATEGORIES, CLEAN_POI_FILE, OUTPUT_FILE  # noqa: E402
from pipeline.prompts import (  # noqa: E402
    MBTI_PERSONA,
    PREFERENCE_MOOD,
    ZODIAC_VIBE,
    validate_copy,
)
from pipeline.signals import build_signals  # noqa: E402

rng = random.Random(20240814)


# --- signal helpers --------------------------------------------------------

def _by(signals: list[dict], source: str) -> dict | None:
    for s in signals:
        if s["source"] == source:
            return s
    return None


def _elevation_m(signals: list[dict]) -> int | None:
    s = _by(signals, "elevation")
    if not s:
        return None
    m = re.search(r"(\d{3,4})", s["fact"])
    return int(m.group(1)) if m else None


def _keywords(signals: list[dict]) -> list[str]:
    """The concrete feature words from the `tag` signal (山间小屋 / 意大利菜 ...)."""
    s = _by(signals, "business.tag")
    if not s:
        return []
    m = re.search(r"关键词是(.+)", s["fact"])
    if not m:
        return []
    return [w.strip() for w in m.group(1).split("、") if w.strip()]


def _cuisine_words(keywords: list[str]) -> list[str]:
    """Keywords that read like food, so `action` can say '尝一口…'."""
    food_like = ("菜", "披萨", "意面", "咖啡", "甜点", "冰淇淋", "牛排",
                 "烤肉", "风味")
    return [k for k in keywords if any(f in k for f in food_like)]


# --- per-category flavour ---------------------------------------------------
# Each category gets its own verbs/nouns so a peak never reads like a cafe.
CATEGORY_FLAVOUR: dict[str, dict] = {
    "hut": {
        "place": "这座山间小屋", "verb": "爬上去歇脚",
        "scene": ["推开木门就是一屋子暖气和汤味", "山腰上难得的一处落脚点"],
        "action": ["点一碗热汤配黑麦面包", "坐下来喝杯热的", "尝盘手工意面"],
    },
    "peak": {
        "place": "这座山头", "verb": "站上去",
        "scene": ["整片白云石山群会一次涌到眼前", "云在脚下走，山在四周立"],
        "action": ["爬到观景点吹会儿风", "找块石头坐下发会儿呆", "把三座山峰拍进一张照片"],
    },
    "lake": {
        "place": "这汪高山湖", "verb": "绕着走一圈",
        "scene": ["湖面绿得像块冷玉，山影全落在水里", "风一停，倒影就清清楚楚"],
        "action": ["沿湖走一圈看倒影", "在水边坐着放空", "找个角度看山映在水里"],
    },
    "cable": {
        "place": "这条缆车", "verb": "坐上去",
        "scene": ["不想爬也没关系，让它替你抬升几百米", "脚一离地，山谷就慢慢矮下去"],
        "action": ["买张票坐到山上站", "上到高处再决定往哪走", "坐上去看谷底变小"],
    },
    "trail": {
        "place": "这条山路", "verb": "走进去",
        "scene": ["走一段，风景自己会说话", "水声会一直陪着你往里走"],
        "action": ["顺着水声往里走", "走到瀑布跟前", "沿路慢慢往上"],
    },
    "village": {
        "place": "这片小镇", "verb": "慢慢逛",
        "scene": ["木屋、尖顶和石头墙，一步一个样", "山谷里的安静，是走进来才有的"],
        "action": ["在石板路上随便走走", "进小教堂坐一会儿", "看看老木屋的窗"],
    },
    "food": {
        "place": "这处山谷小馆", "verb": "坐下来吃点",
        "scene": ["山里一顿热饭，比什么都实在", "从窗子能看见山"],
        "action": ["尝一口本地菜", "配杯浓缩收个尾", "点份苹果卷当甜点"],
    },
}


# The validator forbids digits (they usually mean a leaked rating/price), so we
# render elevation as words instead of "2794米". Buckets of ~250m read naturally.
def _elevation_words(metres: int) -> str:
    if metres >= 2800:
        return "海拔快到三千米"
    if metres >= 2500:
        return "海拔两千五往上"
    if metres >= 2200:
        return "海拔两千二三"
    if metres >= 1900:
        return "海拔近两千米"
    if metres >= 1500:
        return "海拔一千五往上"
    return "海拔一千多米"


# --- persona colour ---------------------------------------------------------

def _persona_hook_prefix(mbti: str) -> str:
    """A short, warm opener grounded in the MBTI nickname/trait."""
    persona = MBTI_PERSONA.get((mbti or "").upper())
    if not persona:
        return rng.choice(["今天", "属于你的一站", "手气不错"])
    nick = persona["nick"]
    # I-types like quiet; E-types like buzz. Keep it light.
    if (mbti or "").upper().startswith("I"):
        return rng.choice([f"{nick}的你", "正好没人打扰", "一个人刚刚好"])
    return rng.choice([f"{nick}的你", "爱跑爱跳的你", "闲不住的你"])


def _mood_tail(prefs: list[str]) -> str:
    if "idle" in prefs:
        return rng.choice(["不赶时间，慢慢来", "什么都不干也行"])
    if "sweat" in prefs:
        return rng.choice(["正好动一动、出出汗", "让腿脚忙起来"])
    if "stroll" in prefs:
        return rng.choice(["随便走走就很好", "溜达着看看"])
    if "forage" in prefs:
        return rng.choice(["顺便把肚子喂饱", "吃顿热的再走"])
    return ""


# --- field builders ---------------------------------------------------------

def _make_hook(poi, signals, flavour, profile) -> str:
    prefix = _persona_hook_prefix(profile.get("mbti", ""))
    kws = _keywords(signals)
    scene_word = kws[0] if kws else flavour["place"].lstrip("这座这汪这条这片这家")
    options = [
        f"{prefix}，去{flavour['place']}{flavour['verb']}",
        f"{prefix}，{flavour['place']}在等你",
        f"{prefix}，这一站给你留了{scene_word}",
    ]
    hook = rng.choice(options)
    return _fit(hook, 8, 28)


def _make_reason(poi, signals, flavour, profile) -> str:
    scene = rng.choice(flavour["scene"])
    ele = _elevation_m(signals)
    kws = _keywords(signals)
    bits = [scene]
    if ele:
        words = _elevation_words(ele)
        if ele >= 2500:
            bits.append(f"{words}，风景是给愿意爬的人留的")
        elif ele >= 1800:
            bits.append(f"{words}，空气会突然变凉")
        else:
            bits.append(f"{words}，当个热身刚好")
    # opening-hours fact is worded ("8点到18点开着"); the validator forbids
    # digits, so translate it rather than drop it entirely.
    open_sig = _by(signals, "opentime")
    if open_sig and len(bits) < 2:
        bits.append("白天开着，赶早不赶晚")
    if kws and len(bits) < 2:
        bits.append("有" + "、".join(kws[:2]) + "，冲着这些去不会跑偏")
    mood = _mood_tail(profile.get("preferences", []))
    reason = f"你{'，'.join(bits[:2])}"
    # Add the mood tail exactly once, if it fits. Then, only if we still fall
    # short of the 25-char floor, add a generic soft tail — never the mood twice.
    if mood and mood not in reason and len(reason) + len(mood) + 1 <= 72:
        reason += "，" + mood
    if len(reason) < 25:
        reason += "，来都来了，值得走这一趟"
    return _fit(reason, 25, 75)


def _make_oracle(poi, signals, flavour, profile) -> str:
    zodiac = profile.get("zodiac", "")
    vibe = ZODIAC_VIBE.get(zodiac, "")
    options = [
        "去吧，今天手气不错",
        "别多想，这一站适合你",
        "会有点小惊喜等着你",
        "山不会亏待走上来的人",
    ]
    if vibe:
        options.append(f"{zodiac}的你，跟着直觉走准没错")
    return _fit(rng.choice(options), 6, 26)


def _make_action(poi, signals, flavour, profile) -> str:
    cuisine = _cuisine_words(_keywords(signals))
    # Only huts/food get a "尝一口X" action, and only when the venue actually
    # carries a food keyword — otherwise fall back to the category's own verbs.
    if cuisine and poi.get("category") in ("hut", "food"):
        act = f"尝一口{cuisine[0]}"
    else:
        act = rng.choice(flavour["action"])
    return _fit(act, 4, 20)


def _fit(text: str, low: int, high: int) -> str:
    """Trim/pad softly to land inside [low, high] without breaking meaning."""
    text = text.strip().strip("，,。.")
    if len(text) > high:
        text = text[:high]
    return text


# --- assembly ---------------------------------------------------------------

def generate_one(poi: dict, profile: dict) -> dict | None:
    signals = build_signals(poi)
    if len(signals) < 2:
        return None
    flavour = CATEGORY_FLAVOUR.get(poi.get("category"), CATEGORY_FLAVOUR["hut"])

    last_reason = ""
    for _ in range(6):  # a few tries to satisfy validate_copy
        copy = {
            "hook": _make_hook(poi, signals, flavour, profile),
            "reason": _make_reason(poi, signals, flavour, profile),
            "oracle": _make_oracle(poi, signals, flavour, profile),
            "action": _make_action(poi, signals, flavour, profile),
        }
        ok, last_reason = validate_copy(copy, signals, profile)
        if ok:
            copy["sources"] = sorted({s["source"] for s in signals})
            return {**poi, **copy}
    generate_one.last_reason = last_reason  # type: ignore[attr-defined]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Dolomites copy generator")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--mbti", default="ENFP")
    parser.add_argument("--zodiac", default="射手座")
    parser.add_argument("--pref", default="sweat",
                        help="one of forage/sweat/stroll/idle/fate")
    args = parser.parse_args()

    profile = {"mbti": args.mbti, "zodiac": args.zodiac,
               "preferences": [args.pref] if args.pref else []}

    pois = json.loads(Path(CLEAN_POI_FILE).read_text(encoding="utf-8"))
    rng.shuffle(pois)

    out: list[dict] = []
    skipped = 0
    reasons: dict[str, int] = {}
    for poi in pois:
        if len(out) >= args.limit:
            break
        copy = generate_one(poi, profile)
        if copy is None:
            skipped += 1
            r = getattr(generate_one, "last_reason", "?")
            reasons[r] = reasons.get(r, 0) + 1
            continue
        out.append(copy)
    if skipped and not out:
        print("skip reasons:", dict(sorted(reasons.items(),
                                           key=lambda kv: -kv[1])[:6]))

    persona = MBTI_PERSONA.get(args.mbti.upper(), {})
    mood = PREFERENCE_MOOD.get(args.pref, "")
    print(f"Persona: {args.mbti}（{persona.get('nick','')}）/ {args.zodiac} / {mood}")
    print(f"Generated {len(out)}, skipped {skipped}\n")

    # Print a readable sample so the effect is visible right in the terminal.
    for item in out[:12]:
        name = item.get("display_name") or item["name"]
        print(f"[{item['category']}] {name}")
        print(f"   hook   : {item['hook']}")
        print(f"   reason : {item['reason']}")
        print(f"   oracle : {item['oracle']}")
        print(f"   action : {item['action']}")
        print()

    # Format into the exact camelCase shape the frontend expects (mirrors
    # local_generate.py / generate.py), otherwise App.tsx reads undefined
    # fields (categoryLabel / openHour / area ...).
    payload = []
    for record in out:
        payload.append({
            "id": record["id"],
            "name": record.get("display_name") or record["name"],
            "category": record["category"],
            "categoryLabel": CATEGORIES[record["category"]]["label"],
            "area": (record.get("business_area") or record.get("adname")
                     or "多洛米蒂"),
            "address": record.get("address", ""),
            "lng": record["lng"],
            "lat": record["lat"],
            "openHour": record.get("open_hour"),
            "closeHour": record.get("close_hour"),
            "durationMinutes": record.get("duration_minutes"),
            "direction": record.get("direction"),
            "rarity": record.get("rarity", "common"),
            "lucky": record.get("lucky", 50),
            "hook": record["hook"],
            "reason": record["reason"],
            "oracle": record["oracle"],
            "action": record["action"],
            "sources": record.get("sources", []),
        })

    dst = Path(OUTPUT_FILE)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"Wrote {len(payload)} records -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
