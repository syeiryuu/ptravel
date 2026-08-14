"""
The 真实数据 -> 玄学语言 translation layer.

This is the answer to "文案不能纯玄学，必须虚实相应".

The rule we enforce everywhere: **the fact is real, only the phrasing is
mystical.** We never invent a fact and dress it up; we take a fact that is
actually in the data and choose a mystical way to say it.

Example of the difference:
    BAD  (pure fantasy)  "下午三点阳光从西面斜过来"   <- we do not know this
    BAD  (review voice)  "评分4.7，人均85元"          <- product forbids this
    GOOD (虚实相应)      "西南方向，日落前一小时抵达最好"
                          ^ direction from real coords
                                    ^ from real closing time + duration

Each function turns one real field into a "signal": a short factual note plus
the mystical angle it licenses. Signals are fed to the LLM, which may only
write from them.
"""

from __future__ import annotations

import math
import re
from datetime import datetime

# --- Fact -> mystical vocabulary -------------------------------------------

DIRECTION_LORE = {
    "正东": "东方主生发，适合开始一件事",
    "东南": "东南为巽，风与顺遂之位",
    "正南": "南方明亮，宜见人、宜热闹",
    "西南": "西南主安顿，适合停下来歇着",
    "正西": "西方收敛，适合一个人待着",
    "西北": "西北主决断，适合想清楚一件事",
    "正北": "北方幽静，适合藏起来",
    "东北": "东北为艮，山止之位，适合慢下来",
}

MOON_LORE = {
    "新月": "月正空着，适合开一个头",
    "峨眉月": "月才起了个角，事情也刚起头",
    "上弦月": "月过半，该做的事别再拖",
    "盈凸月": "月快满了，好事在攒",
    "满月": "月满，今晚不该待在屋里",
    "亏凸月": "月开始退，适合收一收",
    "下弦月": "月过半而缺，宜舍不宜取",
    "残月": "月要走了，旧事该翻篇",
}

WEATHER_LORE = {
    "晴": "光是今天最好的道具",
    "多云": "云会替你把光调软",
    "阴": "没有影子的天，适合看颜色",
    "小雨": "雨小，街上的人会少一半",
    "中雨": "雨天里屋檐下的位置最值钱",
    "大雨": "这种天出门的人，都是有理由的",
    "雪": "雪会把声音吃掉",
    "雾": "看不远的时候，只好看近处",
    "霾": "今天适合待在室内",
}


def hour_lore(hour: int) -> str:
    """Mystical framing for a time of day. Purely a phrasing helper."""
    if 5 <= hour < 8:
        return "天刚亮，城市还没睡醒"
    if 8 <= hour < 11:
        return "上午的光是直的"
    if 11 <= hour < 14:
        return "正午，影子最短的时候"
    if 14 <= hour < 17:
        return "下午的光开始斜"
    if 17 <= hour < 19:
        return "日落前后，光最软的一小时"
    if 19 <= hour < 22:
        return "天黑之后，招牌比白天好看"
    return "夜深了，还开着的地方都有故事"


# --- Signal builders -------------------------------------------------------
# Each returns (fact, angle) or None when the underlying data is missing.
# `fact` must be literally true given the data. `angle` is the licensed
# mystical reading of that fact.


def signal_direction(direction: str | None) -> tuple[str, str] | None:
    if not direction:
        return None
    lore = DIRECTION_LORE.get(direction)
    if not lore:
        return None
    return (f"在你的{direction}方向", lore)


def signal_rating(rating: str | None) -> tuple[str, str] | None:
    """
    Turn a numeric rating into a *qualitative* signal.

    We must never print the number (product rule: no ratings), but the number
    is still evidence: it tells us how confidently we can speak.
    """
    try:
        value = float(rating or "")
    except (TypeError, ValueError):
        return None
    if value >= 4.7:
        return ("去过的人几乎都说好", "这一颗不用犹豫")
    if value >= 4.3:
        return ("口碑稳定", "大概率不会踩空")
    if value >= 3.8:
        return ("评价还行，但没到人人称赞", "适合自己去下个判断")
    return ("评价一般", "去不去，看你今天想不想冒险")


def signal_cost(cost: str | None) -> tuple[str, str] | None:
    """Price band, never the number."""
    try:
        value = float(cost or "")
    except (TypeError, ValueError):
        return None
    if value <= 30:
        return ("花不了几个钱", "低成本的决定，错了也不心疼")
    if value <= 80:
        return ("寻常价钱", "刚好是一次不用计算的消费")
    if value <= 200:
        return ("不算便宜", "适合今天想对自己好一点的时候")
    return ("价格不轻", "这不是随便去的地方，挑个值得的日子")


def signal_tag(tag: str | None, limit: int = 3) -> tuple[str, str] | None:
    """
    A comma-separated list of real feature keywords for a venue. For AMap food
    POIs these are signature dishes; for the OSM Dolomites build they are hard
    attributes like 山峰 / 缆车 / 海拔2100米 / 意大利菜. Either way it is the most
    valuable field we have: concrete, verifiable and unique per venue.

    The phrasing therefore stays neutral ("这里有…") rather than assuming food,
    so "点它，别纠结" doesn't get attached to a mountain peak.
    """
    if not tag or not tag.strip():
        return None
    items = [t.strip() for t in tag.replace("|", ",").split(",") if t.strip()]
    if not items:
        return None
    picked = items[:limit]
    return ("这里的关键词是" + "、".join(picked),
            "冲着这些去，不会跑偏")


# Natural, always-open outdoor features (mountain build): a peak or a lake has
# no "opening hours", so turning its (0,24) default into "开到凌晨24点" is absurd.
# We suppress the opentime signal for these categories.
_NO_HOURS_CATEGORIES = {"peak", "lake", "trail"}


def signal_opentime(open_hour: int | None, close_hour: int | None,
                    now_hour: int | None = None,
                    category: str | None = None) -> tuple[str, str] | None:
    """Timing advice derived from real opening hours."""
    if open_hour is None or close_hour is None:
        return None
    if category in _NO_HOURS_CATEGORIES:
        return None
    # An all-day (0,24) default carries no real information — skip it rather
    # than claim a venue "开到凌晨24点".
    if open_hour == 0 and close_hour >= 24:
        return None
    display_close = close_hour if close_hour <= 24 else close_hour - 24
    if close_hour >= 24:
        return (f"开到凌晨{display_close}点", "夜里还亮着的地方，值得记住")
    if close_hour >= 22:
        return (f"开到{display_close}点", "不用赶，可以慢慢待")
    if open_hour <= 7:
        return (f"{open_hour}点就开门", "早到的人有整个空间")
    if now_hour is not None and close_hour - now_hour <= 2:
        return (f"{display_close}点就打烊", "现在动身刚好，再晚就赶了")
    return (f"{open_hour}点到{display_close}点开着", "时间够，不必着急")


def signal_photos(photo_titles: list[str] | None) -> tuple[str, str] | None:
    """
    Photo titles are user-supplied labels on real photos (e.g. "环境", "门头").
    They hint at what the place is visually known for.
    """
    if not photo_titles:
        return None
    meaningful = [t.strip() for t in photo_titles
                  if t and t.strip() and t.strip() not in ("图片", "默认图片")]
    if not meaningful:
        return None
    return (f"被拍得最多的是{meaningful[0]}", "去了就知道该往哪儿看")


def signal_area(area: str | None) -> tuple[str, str] | None:
    if not area or not area.strip():
        return None
    return (f"在{area}一带", "这一片本身就值得绕两圈")


def signal_elevation(alias: str | None) -> tuple[str, str] | None:
    """
    Elevation, mountain-region only.

    In the Dolomites build the collector stashes an "海拔约2100米" note in the
    POI's `alias` field. Elevation is a hard, checkable fact and it sets the
    whole mood — the higher you go, the bigger the reward and the colder the
    air. We turn it into a signal the copy can lean on.
    """
    if not alias or "海拔" not in alias:
        return None
    match = re.search(r"(\d{3,4})", alias)
    if not match:
        return None
    metres = int(match.group(1))
    if metres >= 2500:
        return (f"这里在海拔{metres}米", "上到这个高度，风景是给愿意爬的人留的")
    if metres >= 1800:
        return (f"这里在海拔{metres}米", "半山之上，空气会突然变凉、变干净")
    return (f"这里在海拔{metres}米", "还没到最高处，正好当个热身")


def signal_weather(weather: str | None, temp: str | None) -> tuple[str, str] | None:
    if not weather:
        return None
    for key, lore in WEATHER_LORE.items():
        if key in weather:
            fact = f"今天{weather}"
            if temp:
                fact += f"，{temp}度"
            return (fact, lore)
    return None


def signal_moon(phase: str | None) -> tuple[str, str] | None:
    if not phase:
        return None
    for key, lore in MOON_LORE.items():
        if key in phase:
            return (f"今晚是{phase}", lore)
    return None


def signal_historic(historic: str | None, inception: str | None) -> tuple[str, str] | None:
    """From a Wikidata inception year, or a generic historic marker."""
    if inception:
        try:
            years = datetime.now().year - int(inception)
        except (TypeError, ValueError):
            years = None
        if years is not None and years >= 100:
            return (f"{inception}年就在这儿了", "站过一个世纪的东西，不会白看")
        if years is not None and years >= 30:
            return (f"{inception}年建的", "比你岁数大的地方，话也多")
        return (f"{inception}年就在这儿了", "旧的东西自带答案")
    if historic:
        return ("这地方有点年头", "旧的东西自带答案")
    return None


def signal_heritage(is_heritage: bool | None) -> tuple[str, str] | None:
    """Wikidata says this is a listed heritage site - a hard, checkable fact."""
    if not is_heritage:
        return None
    return ("这是个被登记在册的地方", "被保下来的东西，总有它的道理")


def signal_dynasty(dynasty: str | None) -> tuple[str, str] | None:
    """
    For places older than record-keeping ("明朝", "清代").

    Used only when no exact founding year exists - a dynasty is vaguer but
    still entirely true, and it carries more atmosphere than a number.
    """
    if not dynasty:
        return None
    return (f"这地方能追到{dynasty}", "有些地方比城市本身还老")


def signal_wiki_description(description: str | None) -> tuple[str, str] | None:
    """
    Wikipedia's one-line description (e.g. "北京市的一座城市公园").

    Short, factual, and often the only thing that tells us *what a place
    actually is* when AMap's type code is too coarse to be useful.
    """
    if not description or not description.strip():
        return None
    text = description.strip()
    if len(text) > 30:
        return None
    return (f"它的身份是：{text}", "先知道它是什么，再决定怎么看它")


def signal_aoi(aoi_name: str | None, poi_name: str | None = None) -> tuple[str, str] | None:
    """
    The named area a POI sits inside ("798艺术区", "三里屯太古里").

    This is a strong signal because it implies there is *more around it*:
    the suggestion stops being a single dot and becomes a place you can wander.
    """
    if not aoi_name or not aoi_name.strip():
        return None
    name = aoi_name.strip()
    # An AOI that just repeats the POI name adds nothing.
    if poi_name and (name in poi_name or poi_name in name):
        return None
    return (f"它在{name}里面", "到了别直奔主目的，周围也算行程")


def signal_sunset(sunset: str | None, close_hour: int | None = None,
                  category: str | None = None) -> tuple[str, str] | None:
    """
    Today's real sunset time.

    This is the signal that finally licenses the 「日落前一小时」 phrasing we
    always wanted but previously had to invent. It only makes sense outdoors,
    and only if the place is still open then.
    """
    if not sunset or ":" not in sunset:
        return None
    if category not in ("park", "weird", "night", None):
        return None
    try:
        hour = int(sunset.split(":")[0])
    except ValueError:
        return None
    if close_hour is not None and close_hour <= hour:
        return None
    return (f"今天{sunset}日落", "提前半小时到，光最好的那段就归你了")


def signal_distance(distance_m: float | None) -> tuple[str, str] | None:
    if distance_m is None:
        return None
    if distance_m <= 500:
        return (f"距你约{int(distance_m)}米", "近到没有借口不去")
    if distance_m <= 1500:
        minutes = max(5, round(distance_m / 80))
        return (f"走过去约{minutes}分钟", "路上的时间也算在体验里")
    km = round(distance_m / 1000, 1)
    return (f"距你{km}公里", "值得为它挪一次窝")


def signal_crowd(poi_density: int | None,
                 lively_count: int | None = None) -> tuple[str, str] | None:
    """
    Density of surrounding POIs (from regeo) as a proxy for how busy the
    area is. Real signal, honestly framed as an impression rather than a
    statistic - we counted shops, we did not count people.

    `lively_count` only counts food/shopping/leisure POIs, so an office park
    with 50 buildings does not read as "热闹".
    """
    if poi_density is None:
        return None
    lively = lively_count if lively_count is not None else poi_density
    if lively >= 25:
        return ("周围一圈都是吃的喝的", "逛不完的话，就当留个下次")
    if lively >= 10:
        return ("周边有几家店作伴", "不至于冷清，也不至于吵")
    if poi_density >= 30:
        return ("周围楼多，店少", "这种地方的安静是意外收获")
    return ("附近很安静", "清净是它最大的优点")


# Signal sources that carry 方位/月相/日落 fortune-telling framing. The new
# copy voice drops these ("去掉方位，改为浅浅的好运感"), so they are excluded by
# default and only re-enabled if a caller explicitly asks for the old style.
MYSTIC_SOURCES = {"direction", "moon", "sunset"}


def build_signals(poi: dict, context: dict | None = None,
                  include_mystic: bool = False) -> list[dict]:
    """
    Assemble every available signal for a POI.

    Returns a list of {source, fact, angle}. `source` names the data field so
    every line of generated copy stays traceable to real data - that is what
    makes the copy defensible rather than invented.

    `include_mystic` controls the 方位/月相/日落 signals. It defaults to False:
    the current copy voice uses only a light "good-luck" touch and no compass /
    moon / sunset fortune-telling, so those signals are left out unless asked.
    """
    context = context or {}
    business = poi.get("business") or {}
    now_hour = context.get("hour", datetime.now().hour)

    # Moon phase and sunset may be attached to the POI at enrich time (build)
    # or supplied per-draw at run time; run-time context wins because it is
    # the more current of the two.
    moon_phase = context.get("moon_phase") or poi.get("moon_phase")
    sunset = context.get("sunset") or poi.get("sunset")

    candidates = [
        ("direction", signal_direction(poi.get("direction"))),
        ("business.tag", signal_tag(business.get("tag") or poi.get("tag"))),
        ("business.rating", signal_rating(business.get("rating") or poi.get("rating"))),
        ("business.cost", signal_cost(business.get("cost") or poi.get("cost"))),
        ("opentime", signal_opentime(poi.get("open_hour"), poi.get("close_hour"),
                                     now_hour, poi.get("category"))),
        ("photos.title", signal_photos(poi.get("photo_titles"))),
        ("business_area", signal_area(poi.get("business_area"))),
        # Mountain-region only: elevation stashed in `alias` by collect_osm.
        ("elevation", signal_elevation(poi.get("alias"))),
        ("distance", signal_distance(poi.get("distance_m"))),
        # --- from enrich.py: regeo ---
        ("poi_density", signal_crowd(poi.get("poi_density"),
                                     poi.get("lively_count"))),
        ("aoi", signal_aoi(poi.get("aoi_name"), poi.get("name"))),
        # --- from enrich.py: wikipedia / wikidata ---
        ("wikidata.inception", signal_historic(poi.get("historic"),
                                               poi.get("inception"))),
        ("wikidata.heritage", signal_heritage(poi.get("is_heritage"))),
        ("wiki.dynasty", signal_dynasty(poi.get("dynasty"))),
        ("wiki.description", signal_wiki_description(poi.get("wiki_description"))),
        # --- from enrich.py / run time: qweather ---
        ("weather", signal_weather(context.get("weather"), context.get("temperature"))),
        ("moon", signal_moon(moon_phase)),
        ("sunset", signal_sunset(sunset, poi.get("close_hour"),
                                 poi.get("category"))),
    ]

    signals = []
    for source, result in candidates:
        if result is None:
            continue
        if not include_mystic and source in MYSTIC_SOURCES:
            continue
        fact, angle = result
        signals.append({"source": source, "fact": fact, "angle": angle})
    return signals
