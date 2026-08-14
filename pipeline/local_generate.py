"""
Offline copy generator - assembles "Grounded Mystic" copy from signals
without calling the LLM.

This exists so the data pipeline can produce differentiated, hallucination-free
copy for testing and development without spending OpenAI quota. The output
follows the exact same schema as generate.py.

The approach: for each POI, pick 2-3 signals and weave them into the
hook/reason/oracle/action fields following the "虚实相应" principle -
facts are real (from signals), phrasing is mystical.

Usage:
    python3 pipeline/local_generate.py [--limit N]

When OPENAI_API_KEY is available, prefer:
    python3 pipeline/generate.py --limit 1000
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import (  # noqa: E402
    CATEGORIES,
    CLEAN_POI_FILE,
    OUTPUT_FILE,
)
from pipeline.prompts import validate_copy  # noqa: E402
from pipeline.signals import build_signals  # noqa: E402

MIN_SIGNALS = 2
rng = random.Random(42)


# --- Signal lookup helpers --------------------------------------------------

def _pick(signals: list[dict], source: str) -> dict | None:
    """Find a signal by source name."""
    for s in signals:
        if s["source"] == source:
            return s
    return None


def _pick_any(signals: list[dict], exclude: set[str] | None = None) -> dict | None:
    """Pick a random signal, optionally excluding certain sources."""
    pool = [s for s in signals if not exclude or s["source"] not in exclude]
    return rng.choice(pool) if pool else None


# --- Template priority system -----------------------------------------------
# Signal rarity weights: rarer signals get priority when available,
# so common signals (moon, direction, opentime) don't dominate.
SIGNAL_RARITY = {
    "wikidata.heritage": 10, "wiki.dynasty": 10, "wikidata.inception": 8,
    "wiki.description": 7, "aoi": 7, "business.tag": 6,
    "photos.title": 5, "sunset": 5, "poi_density": 4,
    "business.rating": 3, "business.cost": 3,
    "direction": 2, "business_area": 2, "opentime": 2,
    "moon": 1, "weather": 1, "distance": 1,
}

# Map each template function to the signal source it depends on
# (the first signal it checks). Templates with no specific dependency
# get weight 0 (lowest priority).
TEMPLATE_SOURCE_MAP: dict = {}


def _register_template(source: str):
    """Decorator: record which signal source a template prefers."""
    def decorator(func):
        TEMPLATE_SOURCE_MAP[func] = source
        return func
    return decorator


# --- Hook templates ---------------------------------------------------------
# Each template is a function that takes (poi, signals) and returns a string
# or None if it can't apply. We randomly try templates until one works.

@_register_template("wikidata.heritage")
def _hook_heritage(poi, signals):
    if not _pick(signals, "wikidata.heritage"):
        return None
    base = ["被登记在册的老地方", "被保下来的去处", "有来历的角落",
            "旧到被记住了的地方", "不会被拆掉的老地方",
            "被时间选中留下来的地方", "旧到有记录的地方",
            "有身份的老去处", "被记住是有原因的地方",
            "旧到值得保护的地方", "有来历不会被遗忘的地方",
            "被写进名录的角落", "旧到需要被记住的地方"]
    suffix = ["", "", "", "去了就知道", "值得专门去一趟"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("wiki.dynasty")
def _hook_dynasty(poi, signals):
    sig = _pick(signals, "wiki.dynasty")
    if not sig:
        return None
    d = poi.get("dynasty", "")
    base = [f"能追到{d}的去处", f"比城市还老的角落", f"{d}留下来的地方",
            f"从{d}撑到现在的地方", f"{d}时候就在了的地方",
            f"{d}的风还在吹的地方", f"能追到{d}的角落",
            f"{d}留下来的去处", f"比记忆还老的角落",
            f"{d}年间的去处", f"{d}就有了的地方",
            f"从{d}活到现在的地方", f"{d}的旧东西还在"]
    suffix = ["", "", "", "去了就知道", "值得专门去一趟"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("aoi")
def _hook_aoi(poi, signals):
    sig = _pick(signals, "aoi")
    if not sig:
        return None
    aoi = poi.get("aoi_name", "")
    base = [f"{aoi}里的角落", f"藏在{aoi}里", f"{aoi}一处歇脚地",
            f"{aoi}不赶时间的地方", f"{aoi}慢下来的去处",
            f"{aoi}藏着的地方", f"{aoi}里值得绕一圈的",
            f"{aoi}里的安静角落", f"{aoi}不用赶的地方",
            f"{aoi}里的歇脚处", f"{aoi}慢走的地方",
            f"{aoi}里不急的去处", f"{aoi}里的角落值得待",
            f"{aoi}里慢慢看的地方", f"{aoi}里能待住的地方"]
    suffix = ["", "", "", "去了就知道", "值得绕一圈"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("business.tag")
def _hook_tag(poi, signals):
    sig = _pick(signals, "business.tag")
    if not sig:
        return None
    tag = poi.get("tag", "")
    items = [t.strip() for t in tag.replace("|", ",").split(",") if t.strip()]
    if not items:
        return None
    item = items[0]
    base = [f"被反复提起的{item}", f"{item}是这里的招牌",
            f"来了就为这口{item}", f"有人专程来吃{item}",
            f"{item}不会让你失望", f"冲着{item}去不亏",
            f"{item}是必点的", f"被安利最多的{item}",
            f"{item}是这里的理由", f"来这不吃{item}亏了",
            f"{item}是来这的原因", f"{item}是必打卡的",
            f"为{item}跑一趟值得", f"{item}是招牌不会错"]
    suffix = ["", "", "", "去了就知道", "别犹豫"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("direction")
def _hook_direction(poi, signals):
    sig = _pick(signals, "direction")
    if not sig:
        return None
    d = poi.get("direction", "")
    base = [f"{d}风向上的去处", f"往{d}走不远处", f"{d}方向的角落",
            f"{d}那个方向有地方待", f"{d}风带着你过去",
            f"{d}边有个去处", f"{d}面值得跑一趟",
            f"往{d}方向走", f"{d}方有东西在等你",
            f"{d}方藏着好去处", f"朝{d}走不远处",
            f"{d}边有地方可待", f"{d}面有个去处",
            f"往{d}边走走", f"{d}方有去处"]
    suffix = ["", "", "", "去了就知道", "不亏"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("business_area")
def _hook_area(poi, signals):
    sig = _pick(signals, "business_area")
    if not sig:
        return None
    area = poi.get("business_area", "")
    base = [f"{area}一带的歇脚处", f"在{area}不远处", f"{area}藏着的地方",
            f"{area}一个安静的角落", f"{area}值得绕一圈的去处",
            f"{area}附近的去处", f"{area}那一片的地方",
            f"{area}里有意思的角落", f"{area}一带值得去",
            f"{area}不远的去处", f"{area}那边的歇脚地",
            f"{area}里能待的地方", f"{area}附近的好去处",
            f"{area}藏着的好地方", f"{area}一带的角落"]
    suffix = ["", "", "", "去了就知道", "值得去一趟"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("poi_density")
def _hook_crowd(poi, signals):
    sig = _pick(signals, "poi_density")
    if not sig:
        return None
    density = poi.get("poi_density", 0)
    lively = poi.get("lively_count", 0)
    if lively >= 25:
        base = ["周围一圈都是吃的喝的", "热闹到逛不完的地方",
                "不缺伴的一个去处", "人气旺的地方有它的道理",
                "能逛能吃能待的地方", "热闹到忘时间的地方",
                "人气旺值得去的地方", "热闹到值回票价的地方",
                "不缺伴的角落", "能逛能吃的地方",
                "人气旺不会错的地方", "热闹到停不下来的地方",
                "周围一圈都值得逛", "热闹到逛不完的角落",
                "能吃能逛能待的地方", "人气旺有道理的角落",
                "热闹到忘时间的地方", "不缺伴值得去的地方",
                "能逛能待的地方", "人气旺的角落"]
        suffix = ["", "", "", "去了就知道", "值得去一趟", "不会错", "不亏"]
        return rng.choice(base) + rng.choice(suffix)
    elif density >= 30 and lively < 10:
        base = ["楼多店少的安静角落", "意外清净的去处",
                "不被打扰的地方", "清净到能听到自己的脚步",
                "楼多但人少的地方", "闹中取静的一个角落",
                "楼多店少反而自在", "意外清净的角落",
                "清净到难得的地方", "不被打扰的角落",
                "楼多人少反而好", "闹中取静的地方",
                "清净到意外的去处", "楼多店少是好事",
                "不被打扰值得去", "安静到意外的地方",
                "楼多但人少自在", "清净到难得的角落",
                "闹中取静值得去", "楼多人少是好事",
                "清净到值得去", "不被打扰的去处"]
        suffix = ["", "", "", "值得去", "去了不后悔", "不会错", "不亏"]
        return rng.choice(base) + rng.choice(suffix)
    elif lively < 10:
        base = ["附近很安静的地方", "清净是它最大的优点",
                "适合一个人待着的地方", "安静到不真实的地方",
                "人少得像包场的地方", "不被打扰的角落",
                "清净到能听到自己", "安静得出奇的地方",
                "人少的地方不着急", "独享的好去处",
                "没什么人知道的角落", "安静是它唯一的标签",
                "少有人去的地方", "清净难得的地方",
                "安静到值得去的地方", "人少到像包场的角落",
                "不被打扰值得去", "清净到不出声的地方",
                "安静到难得的地方", "人少是好事的角落",
                "独享清净的地方", "没什么人的角落",
                "安静是意外收获", "清净到值得去的地方",
                "人少到难得的地方", "适合一个人的去处",
                "安静到不出声的地方", "清净是意外的地方"]
        suffix = ["", "", "", "去了就知道", "值得去一趟", "不会错", "不亏"]
        return rng.choice(base) + rng.choice(suffix)
    else:
        base = ["不冷清也不吵的地方", "刚好有几家作伴的角落",
                "不多不少正好的地方", "人气刚好不闹腾的地方",
                "不冷清也不挤的地方", "刚好能待住的地方",
                "不多不少的地方", "几家店作伴刚好",
                "不冷清也不闹的地方", "人气刚好",
                "正好能待住的地方", "不太冷清也不太吵",
                "刚好的热闹度", "不冷不热正合适的地方",
                "不多不少刚好", "不意不闹正合适",
                "几家作伴刚好", "不冷不热刚好",
                "人气刚好不用抢", "不多不少值得去",
                "不冷清也不吵的角落", "刚好能待的地方",
                "不意不闹的角落", "几家店作伴正合适",
                "刚好的热闹度不会错", "不冷不热值得去"]
        suffix = ["", "", "", "不用挑", "不用犹豫", "不会错", "不亏"]
        return rng.choice(base) + rng.choice(suffix)


@_register_template("moon")
def _hook_moon(poi, signals):
    sig = _pick(signals, "moon")
    if not sig:
        return None
    phase = poi.get("moon_phase", "")
    suffix = ["", "", "", "去了就知道", "值得出门一趟"]
    if "满" in phase:
        base = ["月满之夜该出门", "今晚月满不宜待在屋里",
                "满月夜宜出门", "月圆时宜动",
                "月满时不宜独处", "满月照亮你的路",
                "月圆夜适合出去", "满月时该出门一趟",
                "月满宜动不宜静", "满月夜别待在屋里",
                "圆月夜适合出门", "月满时宜出门",
                "满月夜该出去走走", "月圆时宜出门"]
    elif "新" in phase:
        base = ["月初适合开个头", "新月夜去的去处",
                "新月宜开头", "月初宜动不宜静",
                "新月夜适合开始", "新月时宜起头",
                "月初适合出门", "新月夜宜动",
                "新月时该出门", "月初适合开始",
                "新月夜适合出门", "月初宜动"]
    elif "退" in phase or "亏" in phase or "残" in phase:
        base = ["月开始退了的地方", "宜收不宜取的角落",
                "月退时宜静", "该收的时候了",
                "残月夜宜静不宜动", "月退时该收一收",
                "残月时宜静", "月亏时宜舍不宜取",
                "月退夜宜静", "残月时该收了",
                "月退时不宜动", "亏月时宜静"]
    else:
        base = ["月快满了好事在攒", "月过半该做的事别再拖",
                "快到圆的时候了", "好事快成了别急",
                "月将满宜等", "月未满还在长",
                "好事在等月满时", "月过半宜动",
                "快圆了该准备了", "月将圈宜出门",
                "月未满时宜等", "好事在攒别急",
                "月过半别再拖", "快圆了好事将近",
                "月将满宜动", "月未圆好事在攒"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("sunset")
def _hook_sunset(poi, signals):
    sig = _pick(signals, "sunset")
    if not sig:
        return None
    base = ["赶在日落前到的地方", "光最好的那段归你",
            "日落前一小时抵达最好", "等着看天变色的去处",
            "日落时分最值得去的地方", "天将暗时去最好",
            "踩着日落时间出发", "日落前赶到的地方",
            "天快暗时最值得去", "光将变时出发",
            "日落前那段光归你", "赶在天变色前到",
            "日落时分别错过", "天将暗时宜出发",
            "日落前的光最值钱", "等天变色那一刻",
            "日落前半小时到", "踩着光的时间去",
            "天快暗时该出发", "日落前赶到就好"]
    suffix = ["", "", "", "别错过", "去了就知道", "不亏", "值得去"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("opentime")
def _hook_opentime(poi, signals):
    sig = _pick(signals, "opentime")
    if not sig:
        return None
    close = poi.get("close_hour", 22)
    open_h = poi.get("open_hour", 10)
    if close >= 24:
        base = ["夜里还亮着的地方", "开到凌晨的去处",
                "天黑之后才成立的事", "灯亮着就是在等你",
                "深夜也不赶客的地方", "夜里才开门的去处",
                "深夜还亮着的角落", "天黑之后值得去的地方",
                "灯亮着就是信号", "开到很晚的地方",
                "深夜也不关门的地方", "夜里去也不晚的角落",
                "天黑之后才对味的地方", "深夜才对味的地方",
                "夜里亮着值得去", "开到凌晨的角落",
                "深夜也不赶客值得去", "灯亮着就是叫你去",
                "夜里还开门的地方", "深夜去也不晚",
                "天黑之后才值得去", "深夜才亮着的地方",
                "夜里去才对味", "开到很晚值得去"]
    elif open_h <= 7:
        base = ["早起的人独享的地方", "天刚亮就开门的角落",
                "清晨没什么人的去处", "一大早就值得去的地方",
                "早起独享的角落", "天亮就开门的地方",
                "清晨值得去的地方", "一大早没什么人的角落",
                "早起去最好的地方", "天刚亮就值得去",
                "清晨独享的角落", "一大早去不亏",
                "早起去清净的地方", "天亮就开门值得去",
                "清晨没什么人值得去", "一大早独享的地方",
                "早起去不挤的地方", "天刚亮去最好",
                "清晨去最清净", "一大早开门的角落"]
    elif close >= 22:
        base = ["不用赶时间的地方", "开到很晚的角落",
                "可以慢慢待的去处", "时间宽裕的地方",
                "不着急走的地方", "不用赶时间的角落",
                "开到很晚值得去", "可以慢慢待的地方",
                "时间宽裕值得去", "不着急走的角落",
                "时间够不赶的地方", "可以多待一会的去处",
                "不赶时间慢慢待", "开到很晚的地方",
                "时间宽裕的角落", "不用赶慢慢来的地方",
                "可以待到关门的去处", "时间够不用慌的地方",
                "不着急慢慢待", "时间宽裕可以多待",
                "不用赶时间的去处", "开到很晚的角落值得去",
                "可以慢慢待的角落", "不着急走值得去"]
    else:
        base = ["开着门就值得进", "时间够不必着急的地方",
                "此刻刚好开着门", "开门就是信号",
                "不赶时间的地方", "此刻开门值得去",
                "时间够不用慌", "开着门就是叫你去",
                "此刻正好开门", "不赶时间的角落",
                "开门就是信号该去", "此刻该去的地方",
                "时间够可以慢慢待", "开着门不犹豫",
                "此刻刚好开门", "不赶时间值得去",
                "开门就是叫你去", "此刻正合适",
                "时间够的地方", "开着门就是答案",
                "此刻宜去", "不赶时间慢慢来",
                "此刻该去不必多想", "开着门该去",
                "时间够不必着急", "此刻开门就是信号"]
    suffix = ["", "", "", "不用犹豫", "去了就知道", "不亏", "值得去"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("wiki.description")
def _hook_wiki(poi, signals):
    sig = _pick(signals, "wiki.description")
    if not sig:
        return None
    desc = poi.get("wiki_description", "")
    suffix = ["", "", "", "去了就知道", "值得去一趟"]
    if "公园" in desc:
        base = ["城市里的一片绿地", "能走着消磨时间的地方",
                "有树有路能待一会儿的地方", "不用花钱就能待住的地方",
                "一片能喘口气的绿地", "走着能消磨时间的地方",
                "有树有路的地方", "能待一会儿的绿地",
                "不用花钱能待住的地方", "能喘口气的地方",
                "城市里的绿地角落", "能走着散心的地方",
                "有树的地方能待住", "一片能走走的绿地"]
    elif "美术" in desc or "艺术" in desc:
        base = ["看点东西的安静去处", "脑子会被填满的地方",
                "看完需要静一会儿的地方", "值得发呆的角落",
                "装了些想法的地方", "看点东西的去处",
                "脑子会被填满的角落", "看完需要静一静的地方",
                "值得发呆的地方", "装了想法的角落",
                "看点东西能发呆的地方", "安静看点东西的去处",
                "脑子会被塞满的地方", "装了些东西的地方"]
    elif "博物" in desc:
        base = ["装着旧时光的地方", "值得慢慢看的一处",
                "东西多到看不完的地方", "旧物件聚在一起的地方",
                "能从下午待到天黑的地方", "装着旧时光的角落",
                "值得慢慢看的地方", "东西多到看不完的角落",
                "旧物件聚在一起的去处", "能待很久的地方",
                "旧时光装在一起的地方", "值得花时间看的一处",
                "东西多到值得慢慢看", "旧物件值得去的地方"]
    else:
        base = ["安静地看点东西", "先知道是什么再去的地方",
                "有个身份的地方", "值得先了解再去的地方",
                "不只是一个名字的地方", "安静看点东西的去处",
                "先了解再去的去处", "有身份的角落",
                "值得先知道的地方", "不只是一个名字的角落",
                "有点东西的地方", "值得先了解的角落",
                "安静看东西的地方", "有个身份的去处"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("wikidata.inception")
def _hook_historic(poi, signals):
    sig = _pick(signals, "wikidata.inception")
    if not sig:
        return None
    inception = poi.get("inception", "")
    base = [f"{inception}年就在这儿了", "比你岁数大的地方",
            "有年头的一个去处", "旧东西自带答案的地方",
            "比你老很多的地方", f"从{inception}年撑到现在",
            "时间证明过的地方", f"{inception}年就有了的去处",
            "有年头的地方不会错", f"从{inception}年留到现在的",
            "时间站在它这边的地方", f"{inception}年就在了的角落",
            "比你岁数大的去处", "旧东西有旧道理的地方",
            f"{inception}年就开在那了", "有年头值得去看",
            f"从{inception}年活到现在", "旧到有记录的地方"]
    suffix = ["", "", "", "去了就知道", "值得去一趟"]
    return rng.choice(base) + rng.choice(suffix)


HOOK_TEMPLATES = [
    _hook_heritage, _hook_dynasty, _hook_aoi, _hook_tag,
    _hook_direction, _hook_area, _hook_crowd, _hook_moon,
    _hook_sunset, _hook_opentime, _hook_wiki, _hook_historic,
]


# --- Reason templates -------------------------------------------------------

@_register_template("wikidata.heritage")
def _reason_heritage_aoi(poi, signals):
    """heritage + aoi/area + time"""
    if not _pick(signals, "wikidata.heritage"):
        return None
    parts = ["被保下来的东西总有它的道理"]
    aoi = poi.get("aoi_name")
    area = poi.get("business_area")
    if aoi:
        parts.append(f"它在{aoi}里面")
        parts.append("到了别直奔一个点，周围绕一圈也算行程")
    elif area:
        parts.append(f"在{area}一带，到了别急着走")
    time_sig = _pick(signals, "opentime")
    if time_sig:
        parts.append(time_sig["angle"])
    return "，".join(parts) + "。"


@_register_template("wiki.dynasty")
def _reason_dynasty_crowd(poi, signals):
    """dynasty + crowd + time"""
    if not _pick(signals, "wiki.dynasty"):
        return None
    parts = [f"这地方能追到{poi['dynasty']}，有些地方比城市还老"]
    crowd_sig = _pick(signals, "poi_density")
    if crowd_sig:
        parts.append(crowd_sig["fact"])
    time_sig = _pick(signals, "opentime")
    if time_sig:
        parts.append("不用赶，慢慢看")
    return "，".join(parts) + "。"


@_register_template("wikidata.inception")
def _reason_historic_area(poi, signals):
    """inception + area + opentime"""
    if not _pick(signals, "wikidata.inception"):
        return None
    parts = [f"{poi['inception']}年就在了，旧东西自带答案"]
    aoi = poi.get("aoi_name")
    area = poi.get("business_area")
    if aoi:
        parts.append(f"它在{aoi}里面")
    elif area:
        parts.append(f"在{area}一带")
    crowd_sig = _pick(signals, "poi_density")
    if crowd_sig:
        parts.append(crowd_sig["angle"])
    time_sig = _pick(signals, "opentime")
    if time_sig:
        parts.append(time_sig["angle"])
    return "，".join(parts) + "。"


@_register_template("business.tag")
def _reason_tag_dir_time(poi, signals):
    """tag + direction + opentime"""
    if not _pick(signals, "business.tag"):
        return None
    tag = poi.get("tag", "")
    items = [t.strip() for t in tag.replace("|", ",").split(",") if t.strip()]
    if not items:
        return None
    picked = "、".join(items[:2])
    parts = [f"{picked}是被反复提起的，不必比较，点了就是"]
    dir_sig = _pick(signals, "direction")
    if dir_sig:
        parts.append(f"在你的{poi['direction']}方向")
    crowd_sig = _pick(signals, "poi_density")
    if crowd_sig and rng.random() < 0.5:
        parts.append(crowd_sig["fact"])
    time_sig = _pick(signals, "opentime")
    if time_sig:
        parts.append(time_sig["angle"])
    return "，".join(parts) + "。"


@_register_template("aoi")
def _reason_aoi_crowd_time(poi, signals):
    """aoi + crowd + opentime"""
    if not _pick(signals, "aoi"):
        return None
    aoi = poi.get("aoi_name", "")
    parts = [f"它在{aoi}里面，到了别直奔一个点"]
    parts.append("周围绕一圈也算行程")
    crowd_sig = _pick(signals, "poi_density")
    if crowd_sig:
        parts.append(crowd_sig["fact"])
        parts.append(crowd_sig["angle"])
    time_sig = _pick(signals, "opentime")
    if time_sig:
        parts.append("不必着急，待一会儿再走也来得及")
    return "，".join(parts) + "。"


@_register_template("wiki.description")
def _reason_wiki_dir_time(poi, signals):
    """wiki_description + direction + opentime"""
    if not _pick(signals, "wiki.description"):
        return None
    desc = poi.get("wiki_description", "")
    parts = [f"它的身份是：{desc}"]
    parts.append("先知道它是什么，再决定怎么看")
    dir_sig = _pick(signals, "direction")
    if dir_sig:
        parts.append(f"在你的{poi['direction']}方向")
    crowd_sig = _pick(signals, "poi_density")
    if crowd_sig and rng.random() < 0.4:
        parts.append(crowd_sig["angle"])
    time_sig = _pick(signals, "opentime")
    if time_sig:
        parts.append(time_sig["angle"])
    return "，".join(parts) + "。"


@_register_template("direction")
def _reason_dir_area_crowd(poi, signals):
    """direction + area + crowd + opentime"""
    dir_sig = _pick(signals, "direction")
    area_sig = _pick(signals, "business_area")
    if not dir_sig or not area_sig:
        return None
    parts = [f"在你的{poi['direction']}方向", f"在{poi['business_area']}一带"]
    crowd_sig = _pick(signals, "poi_density")
    if crowd_sig:
        parts.append(crowd_sig["fact"])
        parts.append(crowd_sig["angle"])
    moon_sig = _pick(signals, "moon")
    if moon_sig and rng.random() < 0.3:
        parts.append(moon_sig["fact"])
    time_sig = _pick(signals, "opentime")
    if time_sig:
        parts.append(time_sig["angle"])
    return "，".join(parts) + "。"


@_register_template("sunset")
def _reason_sunset_crowd(poi, signals):
    """sunset + crowd + area"""
    if not _pick(signals, "sunset"):
        return None
    sunset = poi.get("sunset", "")
    parts = [f"今天{sunset}日落"]
    parts.append("提前半小时到，光最好的那段就归你了")
    area_sig = _pick(signals, "business_area")
    if area_sig:
        parts.append(f"在{poi['business_area']}一带")
    crowd_sig = _pick(signals, "poi_density")
    if crowd_sig:
        parts.append(crowd_sig["angle"])
    dir_sig = _pick(signals, "direction")
    if dir_sig:
        parts.append(f"在你的{poi['direction']}方向")
    return "，".join(parts) + "。"


@_register_template("moon")
def _reason_moon_dir_time(poi, signals):
    """moon + direction + area + time"""
    if not _pick(signals, "moon"):
        return None
    phase = poi.get("moon_phase", "")
    parts = [f"今晚是{phase}"]
    moon_sig = _pick(signals, "moon")
    parts.append(moon_sig["angle"])
    dir_sig = _pick(signals, "direction")
    if dir_sig:
        parts.append(f"在你的{poi['direction']}方向")
    area_sig = _pick(signals, "business_area")
    if area_sig:
        parts.append(f"在{poi['business_area']}一带")
    time_sig = _pick(signals, "opentime")
    if time_sig:
        parts.append(time_sig["angle"])
    return "，".join(parts) + "。"


@_register_template("poi_density")
def _reason_crowd_area_time(poi, signals):
    """crowd + area + opentime"""
    crowd_sig = _pick(signals, "poi_density")
    area_sig = _pick(signals, "business_area")
    if not crowd_sig or not area_sig:
        return None
    parts = [f"在{poi['business_area']}一带"]
    parts.append(crowd_sig["fact"])
    parts.append(crowd_sig["angle"])
    dir_sig = _pick(signals, "direction")
    if dir_sig:
        parts.append(f"在你的{poi['direction']}方向")
    time_sig = _pick(signals, "opentime")
    if time_sig:
        parts.append(time_sig["angle"])
    photo_sig = _pick(signals, "photos.title")
    if photo_sig and rng.random() < 0.3:
        parts.append(photo_sig["fact"])
    return "，".join(parts) + "。"


REASON_TEMPLATES = [
    _reason_heritage_aoi, _reason_dynasty_crowd, _reason_historic_area,
    _reason_tag_dir_time, _reason_aoi_crowd_time, _reason_wiki_dir_time,
    _reason_sunset_crowd, _reason_moon_dir_time,
    _reason_dir_area_crowd, _reason_crowd_area_time,
]


# --- Oracle templates -------------------------------------------------------

@_register_template("direction")
def _oracle_direction(poi, signals):
    sig = _pick(signals, "direction")
    if not sig:
        return None
    d = poi.get("direction", "")
    base = [f"{d}有好事发生", f"{d}方向等着你", f"往{d}走不亏",
            f"{d}风带着你过去", f"答案在{d}方",
            f"{d}方有缘", f"往{d}走就对",
            f"{d}边藏着答案", f"跟着{d}风走",
            f"{d}方有好事", f"朝{d}走就对了",
            f"{d}面有去处", f"{d}方宜动",
            f"往{d}出发不亏", f"{d}方在等你",
            f"{d}边值得去", f"跟着{d}风不亏",
            f"{d}方有东西", f"朝{d}走有好事",
            f"{d}方向宜动", f"往{d}方去就对"]
    suffix = ["", "", "", "别犹豫", "去了就知道", "不亏", "别多想"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("moon")
def _oracle_moon(poi, signals):
    if not _pick(signals, "moon"):
        return None
    phase = poi.get("moon_phase", "")
    suffix = ["", "", "", "别犹豫", "去了就知道"]
    if "满" in phase:
        base = ["月满则至不必犹豫", "月满夜宜出门",
                "满月照亮你的路", "月圆夜宜出门",
                "满月时该出去一趟", "月满宜动不宜静",
                "满月夜别待在屋里", "圆月时宜出门",
                "月满时宜动", "满月夜适合出去",
                "月圆时该出去", "满月时别犹豫",
                "月满夜宜动", "圆月时照亮你的路",
                "满月时该出门", "月圆夜适合出门",
                "满月夜不亏", "月满时宜出门"]
    elif "新" in phase:
        base = ["新月宜开头", "月初该起个头",
                "新月夜适合开始", "月初宜动不宜静",
                "新月时宜起头", "月初适合出门",
                "新月夜宜动", "新月时该出门",
                "月初适合开始", "新月夜适合出门",
                "月初宜动", "新月时宜开始",
                "月初该出门", "新月夜宜出门",
                "月初适合起头", "新月时该开始"]
    elif "退" in phase or "亏" in phase or "残" in phase:
        base = ["宜舍不宜取", "月开始退该收一收",
                "旧事该翻篇了", "月退时宜静不宜动",
                "该收的时候了", "月退时宜静",
                "残月时宜静", "月亏时宜舍不宜取",
                "月退夜宜静", "残月时该收了",
                "月退时不宜动", "亏月时宜静",
                "残月夜宜收", "月退时该收一收",
                "旧事该了了", "残月时宜舍不宜取",
                "月退夜该静", "亏月时宜收"]
    else:
        base = ["好事在攒别急", "该做的事别再拖",
                "月快满了等一等", "快到圆的时候了",
                "好事快成了别急", "月将满好事将近",
                "快到头了别放弃", "月未满宜等",
                "好事在等月满时", "月快满了别急",
                "该做的事别拖了", "好事在攒",
                "月将满宜等", "快圆了好事将近",
                "月未满时宜等", "好事快成了",
                "月过半别再拖", "快到圆了别急",
                "月将满该准备了", "好事在等月圆",
                "月未圆好事在攒", "该做的事该做了"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("wikidata.heritage")
def _oracle_heritage(poi, signals):
    if not _pick(signals, "wikidata.heritage"):
        return None
    base = ["旧的东西自带答案", "被保下来的不会错",
            "老地方有老道理", "被记住的地方不会错",
            "旧东西有旧智慧", "被保下来的值得去",
            "旧东西不会骗你", "老地方不会白去",
            "被记住有被记住的道理", "旧到值得保护不会错",
            "有来历的地方有底气", "被保下来就是答案",
            "旧东西自带智慧", "老地方值得专门去",
            "被记住的地方值得去", "旧到被记住不会错",
            "有来历不会白去", "被保护的就是好的"]
    suffix = ["", "", "", "去了就知道", "别犹豫"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("wiki.dynasty")
def _oracle_dynasty(poi, signals):
    if not _pick(signals, "wiki.dynasty"):
        return None
    base = ["比城市还老的地方有话说", "旧到深处自有光",
            "时间站在你这边", "旧东西不会白看",
            "老地方有老道理", "比记忆还老的地方不会错",
            "旧到深处有答案", "时间证明过不会错",
            "比城市老的地方值得去", "旧东西有旧智慧",
            "老地方不会白去", "时间站在它这边",
            "旧到深处不会错", "比城市还老有它的道理",
            "旧东西自带答案", "老地方值得专门去",
            "时间检验过的地方", "旧到有记录不会错"]
    suffix = ["", "", "", "去了就知道", "别犹豫"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("wikidata.inception")
def _oracle_historic(poi, signals):
    if not _pick(signals, "wikidata.inception"):
        return None
    base = ["旧东西自带答案", "有年头的地方不会白看",
            "比你老的东西话也多", "时间站在你这边",
            "旧东西有旧道理", "老东西不会骗你",
            "时间检验过的地方", "旧的不代表差",
            "有年头值得专门去", "旧东西有旧智慧",
            "时间证明过不会错", "比你老的地方有话说",
            "旧到有记录不会错", "有年头不会白去",
            "时间站在它这边", "旧东西不会白看",
            "老地方值得去", "时间检验过的不会错",
            "比你岁数大的地方有底气", "旧东西自带智慧"]
    suffix = ["", "", "", "去了就知道", "别犹豫"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("aoi")
def _oracle_aoi(poi, signals):
    if not _pick(signals, "aoi"):
        return None
    base = ["到了别直奔一个点", "周围也算行程",
            "先绕一圈再进去", "慢走比快看重要",
            "走到哪算哪也行", "到了先走一圈",
            "别急着找到那个点", "慢慢绕一圈再说",
            "不急先走一圈", "周围绕一圈看看",
            "先走一圈再决定", "慢慢看比快看重要",
            "走到哪看到哪也行", "先走外围再说",
            "不急慢慢绕一圈", "到了先转一圈",
            "别直奔一个点", "先绕一圈看看",
            "到了先散散步", "别急慢慢逛",
            "走到哪算哪也行", "先走一圈再说",
            "到了慢慢转", "不用找慢慢逛",
            "到了先绕一圈", "不急先转转",
            "周围值得走走", "到了不急先看看",
            "慢慢走比快看好", "先走一圈再说",
            "到了先逛逛", "周围值得绕一圈",
            "到了先走一圈再说", "不急先绕绕",
            "到了先走外围", "别急先走一圈",
            "慢慢逛就好", "到了先看看周围",
            "走到哪看到哪", "先走一圈看看",
            "到了慢慢看", "不急慢慢走",
            "周围慢慢转", "到了先走一圈看看",
            "慢慢走一圈", "到了不急慢慢逛"]
    suffix = ["", "", "", "就好", "再说", "慢慢看", "不急", "别赶", "随便逛逛"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("poi_density")
def _oracle_crowd(poi, signals):
    sig = _pick(signals, "poi_density")
    if not sig:
        return None
    lively = poi.get("lively_count", 0)
    density = poi.get("poi_density", 0)
    suffix = ["", "", "", "去了就知道", "别犹豫", "不亏", "不会错"]
    if lively >= 25:
        base = ["逛不完就留个下次", "热闹的地方不缺理由",
                "人多但不会白来", "热闹有热闹的道理",
                "去了就懂为什么人多", "人气旺有它的道理",
                "热闹到忘时间", "人多的地方不会白去",
                "热闹的地方值得去", "逛不完正常",
                "人多说明值得", "热闹不会让你白来",
                "人气旺不会错", "热闹有它的道理",
                "人多的地方有理由", "逛不完就留下次",
                "热闹到值回票价", "人多的地方不会错",
                "热闹是它的招牌", "人气旺值得去",
                "人多不会白去", "热闹有理由",
                "人气旺的地方值得", "逛不完也值得",
                "热闹不会白来", "人多的地方值得去",
                "人气旺不会白去", "热闹到值得去"]
    elif density >= 30 and lively < 10:
        base = ["安静是意外收获", "清净是最大的礼物",
                "楼多店少反而自在", "这份清净值得留",
                "不被打扰就是赚到", "清净难得值得去",
                "楼多店少是好事", "安静是意外之喜",
                "清净到难得", "不被打扰就是赚到",
                "楼多但人少自在", "这份清净值得去",
                "清净是礼物", "安静到值得去",
                "楼多店少反而好", "清净难得不会白去",
                "不被打扰值得去", "安静是最大的收获",
                "清净到不真实", "楼多人少反而好",
                "安静是意外", "清净值得留",
                "楼多店少自在", "不被打扰就是赚到",
                "清净难得", "安静意外收获",
                "这份清净难得", "楼多人少是好事",
                "安静到值得", "清净值得去",
                "不被打扰是赚到", "安静意外之喜"]
    elif lively < 10:
        base = ["清净是它最大的优点", "安静的地方有真东西",
                "人少的地方不着急", "安静比什么都值钱",
                "适合一个人待着的地方", "清净难得的地方有真东西",
                "安静是礼物", "人少时去最好",
                "独享清净的地方", "安静到只剩自己",
                "没什么人的地方最值得", "清净是最大的收获",
                "安静到值得去", "人少的地方慢慢看",
                "清净难得不会白去", "安静到不真实",
                "适合一个人去", "清净是它最大的优点",
                "人少是好事", "安静到难得",
                "独享清净值得去", "没什么人的地方慢慢看",
                "安静有真东西", "清净难得值得",
                "人少值得去", "安静是好事",
                "清净是优点", "适合一个人待着",
                "安静比什么都好", "人少慢慢看",
                "独享清净", "清净有真东西",
                "安静是最大的优点", "人少时不着急",
                "没什么人的地方值得", "清净难得的地方值得"]
    else:
        base = ["不冷清也不吵刚好", "有几家作伴就够了",
                "不多不少正合适", "热度刚好不必担心",
                "人不多不少的地方", "刚好的热闹度",
                "不意不闹正合适", "几家作伴刚好",
                "不冷不热正好", "人气刚好不用抢",
                "不多不少刚好", "不冷不热正合适",
                "热度刚好", "几家作伴正合适",
                "不意不闹刚好", "人不多不少正合适",
                "刚好的热闹度不会错", "不冷清也不吵正合适",
                "人气刚好", "不多不少值得去",
                "不冷不热值得去", "几家店作伴刚好",
                "不冷不热刚好", "不意不闹值得去",
                "人气刚好值得去", "不多不少不会错",
                "热度刚好值得去", "几家店作伴正合适",
                "不冷清也不吵值得去", "人气刚好不会错",
                "刚好的热度", "不意不闹不会错",
                "不多不少刚好", "不冷不热不会错",
                "几家作伴值得去", "不冷清也不闹刚好"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("sunset")
def _oracle_sunset(poi, signals):
    if not _pick(signals, "sunset"):
        return None
    base = ["光最好的那段归你", "赶在日落前到就好",
            "等天变色那一刻", "日落前后最值得去",
            "天将暗未暗时最好", "跟着光走就对了",
            "等天色变了再走", "日落前那段光最值",
            "赶在天变色前到", "日落时分宜出发",
            "光将变时宜动", "日落前赶到就好",
            "天快暗时最值得", "日落前的光归你",
            "等天变色就对了", "日落前宜到",
            "天将暗时宜去", "跟着光走不会错",
            "日落前那段归你", "天快暗时别错过",
            "光最好时宜到", "等天变色那一刻最好",
            "日落前后宜去", "天将暗未暗时宜去",
            "光将变时值得去", "日落时分别错过"]
    suffix = ["", "", "", "别错过", "去了就知道", "不亏", "不会错"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("opentime")
def _oracle_opentime(poi, signals):
    if not _pick(signals, "opentime"):
        return None
    close = poi.get("close_hour", 22)
    open_h = poi.get("open_hour", 10)
    suffix = ["", "", "", "别犹豫", "去了就知道", "不亏", "不会错", "别多想", "正好"]
    if close >= 24:
        base = ["夜里亮着的地方有故事", "天黑之后才成立",
                "深夜去也不晚", "灯亮着就是信号",
                "夜里才对味的地方", "深夜去更对味",
                "夜里亮着值得去", "天黑之后宜去",
                "灯亮着就是叫你去", "深夜也有去处",
                "夜里去不亏", "天黑之后才对味",
                "灯亮着不会错", "夜里去有故事",
                "深夜宜去", "夜里亮着就是答案",
                "天黑之后值得去", "深夜去刚好",
                "夜里去才对味", "灯亮着宜去",
                "深夜去有故事", "夜里亮着宜去",
                "入夜去才对味", "深夜的灯不骗人",
                "夜里去正好", "天黑之后去刚好",
                "灯亮着就是叫你去", "深夜去不会白跑",
                "夜里去有它的道理", "天黑之后别犹豫",
                "深夜去值得", "灯亮着就是答案",
                "夜里亮着就是叫你去", "入夜去不亏",
                "深夜去就对了", "夜里去不会错",
                "天黑之后值得去", "深夜去有它的道理"]
    elif close >= 22:
        base = ["不用赶可以慢慢待", "时间够不必着急",
                "待到关门也不慌", "不赶时间就是赚到",
                "时间充裕不用慌", "慢慢待不着急",
                "时间够多待会儿", "不用赶时间慢慢待",
                "时间够可以多待", "不着急慢慢来",
                "时间充裕值得去", "待到关门也不急",
                "时间够不用慌", "慢慢待就是赚到",
                "不赶时间值得去", "时间够可以慢慢看",
                "时间充裕不着急", "不赶时间宜去",
                "时间够多待一会儿", "慢慢待不会错",
                "时间充裕慢慢来", "不着急值得去",
                "待到关门不慌", "时间够慢慢待",
                "不赶时间慢慢待", "时间够不着急",
                "可以待到关门", "时间够可以多待一会",
                "不赶时间慢慢看", "时间充裕可以多待",
                "慢慢待值得去", "时间够不用赶",
                "可以慢慢待", "时间够慢慢看",
                "不着急可以多待", "时间够待到关门",
                "不用赶慢慢来", "时间充裕不赶",
                "可以多待一会", "慢慢待就好",
                "时间够可以慢慢待", "不赶时间不慌",
                "时间够慢慢来", "可以待到关门不慌"]
    elif open_h <= 7:
        base = ["早起独享", "清晨最好",
                "天亮就去", "一大早不挤",
                "早起去清净", "清晨独享",
                "天刚亮去最好", "一大早去不亏",
                "早起去不挤", "清晨去刚好",
                "赶早去独享", "清晨去不亏",
                "天亮就出门", "早起去最好",
                "清晨去清净", "天刚亮去",
                "赶早不赶晚", "清晨去不挤",
                "早起去刚好", "一大早独享",
                "天亮就值得去", "清晨去值得",
                "早起去不会错", "清晨独享的角落",
                "天刚亮就值得去", "一大早去正好",
                "早起去正好", "清晨去最好",
                "赶早去最好", "天亮就去不亏",
                "清晨去不会白去", "早起去有道理",
                "天刚亮去不亏", "清晨去刚好不挤"]
    else:
        base = ["此刻该去不必多想", "开着门就是信号",
                "刚好开门别犹豫", "此刻正合适",
                "现在去刚刚好", "开门就是叫你去",
                "此刻正好开门", "去就对了",
                "此刻宜动", "此刻开门别犹豫",
                "现在去正合适", "开着门值得去",
                "此刻该去", "开门就是信号该去",
                "此刻刚好开门", "现在去不会错",
                "此刻宜去", "此刻正合适别犹豫",
                "现在去刚好", "开着门就是答案",
                "此刻该去不犹豫", "此刻宜动别多想",
                "现在去就对了", "此刻开门就是信号",
                "此刻该去不会错", "开着门该去",
                "此刻刚好", "开门就是答案",
                "现在去正好", "此刻宜动别犹豫",
                "开着门别犹豫", "此刻开门宜去",
                "现在去该去", "此刻正好去",
                "开着门就是叫你去", "此刻该去正好",
                "现在去不会白跑", "开门就是该去",
                "此刻宜去不犹豫", "现在去刚好开门",
                "此刻正合适宜去", "开着门不会错",
                "此刻开门去就对了", "现在去宜动",
                "此刻刚好宜去", "开门就是信号别犹豫",
                "此刻该去去了就知道", "现在去正好开门"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("wiki.description")
def _oracle_wiki(poi, signals):
    if not _pick(signals, "wiki.description"):
        return None
    base = ["先了解再去看", "知道是什么再去",
            "有个身份的地方值得去", "先知道它是什么",
            "不只是一个名字", "先了解再决定",
            "知道身份再看就不一样", "先了解再去不亏",
            "有个身份不会错", "先知道是什么再说",
            "先了解值得去", "知道是什么再去就对了",
            "先了解就不会白去", "不只是一个名字值得去",
            "先知道它是什么再去", "了解再去看不亏",
            "有个身份有底气", "先了解再去看就好",
            "知道是什么不会错", "先了解再说",
            "有个身份值得去", "先了解就不会错",
            "知道是什么就不会白去", "先了解再去正好",
            "有个身份宜去", "先了解别犹豫",
            "先知道它是什么再去", "了解再看不会错"]
    suffix = ["", "", "", "去了就知道", "别犹豫", "不亏", "不会错", "正好"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("business_area")
def _oracle_area(poi, signals):
    if not _pick(signals, "business_area"):
        return None
    area = poi.get("business_area", "")
    base = [f"{area}值得绕一圈", f"{area}那一片值得去",
            f"{area}一带不会白去", f"{area}藏着好去处",
            f"{area}附近值得走走", f"{area}不远处有答案",
            f"{area}那片值得去", f"{area}附近不会错",
            f"{area}值得去一趟", f"{area}一带有去处",
            f"{area}不会白去", f"{area}那一片不会错",
            f"{area}附近值得去", f"{area}藏着不会错",
            f"{area}值得绕两圈", f"{area}那片有去处",
            f"{area}附近有它的道理", f"{area}那片不会白去",
            f"{area}一带值得", f"{area}不远处不会错",
            f"{area}值得走走", f"{area}那片值得绕一圈",
            f"{area}附近宜去", f"{area}不会错",
            f"{area}那片值得", f"{area}一带不会错",
            f"{area}附近有去处", f"{area}那片值得走走",
            f"{area}藏着有道理", f"{area}一带宜去"]
    suffix = ["", "", "", "去了就知道", "别犹豫", "不亏", "不会错"]
    return rng.choice(base) + rng.choice(suffix)


@_register_template("photos.title")
def _oracle_photo(poi, signals):
    if not _pick(signals, "photos.title"):
        return None
    titles = poi.get("photo_titles", [])
    meaningful = [t for t in titles if t and t not in ("图片", "默认图片")]
    if not meaningful:
        return None
    t = meaningful[0]
    base = [f"去了先找{t}", f"看到{t}就算到了",
            f"{t}是线索", f"奔着{t}去不会错",
            f"找到{t}就对了", f"{t}是目标",
            f"先找{t}再说", f"看到{t}就值了",
            f"{t}不会让你白跑", f"去找{t}不会错",
            f"{t}是线索去了就知道", f"奔着{t}去就对了",
            f"找到{t}就算到了", f"{t}是线索不会错",
            f"去了先找{t}就对了", f"看到{t}就算到",
            f"{t}是线索别犹豫", f"去找{t}就对了",
            f"{t}不会白找", f"先找到{t}再说",
            f"{t}是线索不亏", f"奔着{t}去不亏",
            f"找到{t}不会错", f"看到{t}就值",
            f"去了先找{t}不会错", f"{t}是线索值得去"]
    suffix = ["", "", "", "去了就知道", "别犹豫", "不亏", "不会错"]
    return rng.choice(base) + rng.choice(suffix)


ORACLE_TEMPLATES = [
    _oracle_direction, _oracle_moon, _oracle_heritage, _oracle_dynasty,
    _oracle_historic, _oracle_aoi, _oracle_crowd, _oracle_sunset,
    _oracle_opentime, _oracle_wiki, _oracle_area, _oracle_photo,
]


# --- Action templates -------------------------------------------------------

@_register_template("business.tag")
def _action_tag(poi, signals):
    if not _pick(signals, "business.tag"):
        return None
    tag = poi.get("tag", "")
    items = [t.strip() for t in tag.replace("|", ",").split(",") if t.strip()]
    if not items:
        return None
    item = items[0]
    opts = ["点一份" + item, "先来那口" + item, "就冲" + item + "去",
            "进了就点" + item, item + "是必点的",
            "别犹豫直接点" + item, "先点" + item + "再说",
            "就为" + item + "去", item + "不会错",
            "直接点" + item, item + "是来这的理由",
            "到了先点" + item, "冲着" + item + "去",
            item + "是招牌", "别犹豫点" + item,
            item + "是必吃的", "就冲着" + item + "去"]
    suffix = ["", "", "", "就好", "再说", "不犹豫"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("aoi")
def _action_aoi(poi, signals):
    if not _pick(signals, "aoi"):
        return None
    opts = ["先绕外围走一圈", "到了别直奔一个点", "周围绕一圈再进去",
            "从外围慢慢看起", "先走一圈再决定进不进",
            "别急着找到那个点", "慢慢绕一圈再说",
            "不急先走一圈", "周围绕一圈看看",
            "不急着进去先转转", "到了先转一圈",
            "先走一圈看看", "慢慢绕一圈",
            "先看外围再说", "到了先走一圈",
            "别急先绕一圈", "慢慢看外围",
            "先转一圈再说", "不急慢慢绕",
            "到了慢慢走一圈", "先走外围看看",
            "到了先逛逛再说", "别急慢慢逛",
            "先走外围看看再说", "到了先散散步",
            "周围走走再进去", "到了慢慢转",
            "先走一圈再进去", "别急先转转",
            "到了先走一圈再说", "不急先绕绕",
            "到了先走外围", "到了先走走",
            "慢慢逛就好", "到了先看看周围",
            "先走一圈再说", "到了慢慢看",
            "周围慢慢转", "到了不急先走走",
            "慢慢走一圈", "到了先走一圈看看",
            "到了不急慢慢逛", "先走外围",
            "别急先走一圈", "慢慢走外围",
            "到了先转一圈再说", "先绕一圈",
            "到了先走一圈再决定", "不急先走一圈看看",
            "到了慢慢逛一圈", "先走一圈慢慢看",
            "周围先走走", "到了先看看"]
    suffix = ["", "", "", "就好", "再说", "不急", "慢慢看", "别赶", "随便逛逛"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("poi_density")
def _action_crowd(poi, signals):
    sig = _pick(signals, "poi_density")
    if not sig:
        return None
    lively = poi.get("lively_count", 0)
    density = poi.get("poi_density", 0)
    suffix = ["", "", "", "就好", "再说", "不急", "慢慢看", "别赶", "随便逛逛"]
    if lively >= 25:
        opts = ["先逛一圈再说", "挑个人少的时候去",
                "逛不完就留个下次", "先转一圈摸摸底",
                "人多的地方先走一圈", "找个间隙再进去",
                "逛一圈再说", "挑人少时去",
                "先走一圈看看", "找个间隙进去",
                "人多先转一圈", "逛不完留下次",
                "先摸摸底", "找个间隙去",
                "先逛一圈看看再说", "挑个人少的间隙去",
                "人多先走一圈再说", "逛不完留个下次",
                "找个间隙进去再说", "先转一圈看看",
                "挑人少的时候进去", "先逛一圈",
                "摸摸底再说", "找个间隙",
                "人多先走一圈", "逛不完留下次再说",
                "挑个人少时", "先走一圈",
                "找间隙进去", "先逛一圈摸摸底",
                "人多的地方先走一圈再说", "挑人少的间隙",
                "先转一圈", "逛不完留下次去",
                "找间隙再进去", "先逛一圈看看",
                "挑人少时去再说", "人多先转一圈看看",
                "找个间隙进去看看", "先逛一圈再决定",
                "逛不完就留个下次再说", "挑人少时去就对了"]
    elif density >= 30 and lively < 10:
        opts = ["找个角落坐下来", "享受这份清净",
                "不被打扰地待会儿", "找个没人的位置",
                "清净难得慢慢享受", "挑个安静的角落",
                "找个安静的位置", "清净难得慢慢待",
                "找个没人的角落", "不被打扰地待着",
                "挑个角落坐下", "享受清净",
                "找个安静的地方", "清净慢慢享受",
                "找个没人的地方", "清净难得慢慢看",
                "挑个角落待着", "不被打扰地坐会儿",
                "清净难得值得待", "找个角落慢慢待",
                "享受安静", "不被打扰地看",
                "找个安静角落坐下", "清净难得不急",
                "找个没人的位置坐下", "不被打扰地慢慢待",
                "挑个安静的角落坐下", "清净难得别浪费",
                "找个没人的地方待着", "享受清净慢慢看",
                "清净难得坐下来", "找个角落",
                "清净难得待会儿", "不被打扰地待着就好",
                "找个安静的位置坐下", "清净慢慢待",
                "挑个安静的地方", "不被打扰地看一会儿",
                "找个角落坐下慢慢待", "清净难得找个角落",
                "享受清净不急", "找个没人的角落待着"]
    elif lively < 10:
        opts = ["找个角落坐下来", "享受安静",
                "一个人待一会儿", "找个没人的时候去",
                "安静的地方慢慢待", "独享这份清净",
                "找个安静的位置", "一个人慢慢待",
                "安静地待会儿", "找个人少的时候去",
                "独享安静", "安静慢慢待",
                "找个角落待着", "一个人待着",
                "安静地坐会儿", "独享清净",
                "找个没人的时候", "安静地待着",
                "一个人待一会儿就好", "安静地慢慢看",
                "找个角落坐会儿", "独享安静慢慢待",
                "找个安静位置坐下来", "人少时去",
                "安静的地方待会儿", "独享清净不急",
                "找个没人的角落待着", "一个人安静地待",
                "安静地慢慢待", "找个人少时去",
                "独享这份安静", "找个角落慢慢看",
                "安静的地方坐会儿", "一个人待着不急",
                "安静地看一会儿", "独享清净慢慢待",
                "找个没人的时候去就好", "安静的地方不急",
                "一个人慢慢看", "找个安静位置待着",
                "安静地坐下来", "独享安静就好"]
    else:
        opts = ["进去转一圈", "随便逛逛就好", "看看有什么",
                "不赶时间随便看看", "挑个顺眼的位置",
                "进去看看再说", "随便逛逛",
                "进去转转", "看看再说",
                "不赶时间慢慢看", "挑个位置待着",
                "随便看看就好", "进去走走",
                "看看有什么再说", "挑个地方坐",
                "进去逛逛", "随便看看",
                "挑个位置坐下", "不赶时间逛逛",
                "进去看看", "慢慢逛逛",
                "挑个顺眼的地方", "进去走走看看",
                "随便逛逛再说", "看看再说",
                "不赶时间看看", "挑个地方待着",
                "进去转转看看", "随便走走",
                "慢慢看看", "挑个位置",
                "进去随便逛逛", "看看再说就好",
                "不赶时间转转", "挑个地方坐下来",
                "进去慢慢看", "随便逛逛不急",
                "挑个顺眼的位置坐下", "慢慢逛逛就好",
                "不赶时间进去看看", "看看有什么再说就好",
                "随便走走看看", "进去逛逛再说",
                "慢慢看", "挑个地方坐坐",
                "进去转转就好", "随便逛逛看看"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("sunset")
def _action_sunset(poi, signals):
    if not _pick(signals, "sunset"):
        return None
    opts = ["赶在日落前到", "找朝西的位置等光", "提前半小时到",
            "找个能看天的地方等着", "算好时间再出发",
            "踩着日落的时间去", "等天变色再出发",
            "日落前赶到就好", "找朝西的位置坐下来",
            "踩着光的时间去", "天快暗时出发",
            "找个朝西的地方", "算好时间出发",
            "日落前到就好", "找朝西的位置",
            "提前半小时到就好", "找个能看天的地方",
            "等天变色再去", "踩着日落时间出发",
            "天快暗时去", "日落前出发",
            "找朝西的位置等着", "算好时间到"]
    suffix = ["", "", "", "就好", "再说", "不急"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("photos.title")
def _action_photo(poi, signals):
    if not _pick(signals, "photos.title"):
        return None
    titles = poi.get("photo_titles", [])
    meaningful = [t for t in titles if t and t not in ("图片", "默认图片")]
    if not meaningful:
        return None
    t = meaningful[0]
    opts = [f"去看看{t}", f"先找到{t}", f"奔着{t}去",
            f"找到{t}就算到了", f"{t}是线索",
            f"到了先找{t}", f"去看看那个{t}",
            f"找到{t}再说", f"冲着{t}去",
            f"{t}是目标", f"先找{t}",
            f"奔着{t}去就好", f"看看{t}",
            f"找到{t}就好", f"去找{t}"]
    suffix = ["", "", "", "就好", "再说"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("wikidata.heritage")
def _action_heritage(poi, signals):
    if not _pick(signals, "wikidata.heritage"):
        return None
    opts = ["慢慢走一圈", "先看再看", "别急着走完",
            "慢慢看不赶时间", "走到哪看到哪",
            "留时间给旧东西", "慢慢看别赶",
            "给旧东西留点时间", "看到哪算哪",
            "不赶时间慢慢看", "慢慢走慢慢看",
            "别急慢慢看", "留时间慢慢看",
            "走到哪看到哪也行", "慢慢看不急",
            "先看再说", "不赶时间看",
            "慢慢走别急", "留点时间给旧东西",
            "看到哪算哪也行"]
    suffix = ["", "", "", "就好", "再说"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("wikidata.inception")
def _action_historic(poi, signals):
    if not _pick(signals, "wikidata.inception"):
        return None
    opts = ["慢慢看别赶", "留时间给旧东西", "走到哪看到哪",
            "不赶时间慢慢看", "给旧东西留点时间",
            "看到哪算哪", "慢慢走慢慢看",
            "别急慢慢看", "留时间慢慢看",
            "走到哪看到哪也行", "慢慢看不急",
            "先看再说", "不赶时间看",
            "慢慢走别急", "留点时间给旧东西",
            "看到哪算哪也行", "慢慢看别赶时间",
            "给旧东西留时间", "不急慢慢看"]
    suffix = ["", "", "", "就好", "再说"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("moon")
def _action_moon(poi, signals):
    if not _pick(signals, "moon"):
        return None
    opts = ["抬头看看月亮", "找个能看天的地方", "坐下来等天黑",
            "抬头看看天", "找个能看月的地方坐坐",
            "等天黑了看月亮", "找个开阔的地方看天",
            "坐下来等月亮出来", "找个看得见天的地方",
            "抬头看天", "找个能看天的地方坐着",
            "等月亮出来", "找个开阔地方",
            "坐下来看看天", "抬头找找月亮",
            "找个能看到天的地方", "等天黑了抬头看",
            "找个能看到月的地方", "坐下来抬头看天",
            "找个开阔的角落", "等天黑看月亮",
            "抬头找月亮", "找个能看天的位置",
            "坐着等天黑", "找个看得到天的地方",
            "抬头看看月", "找个看得见月的地方",
            "坐着等月亮出来", "找个开阔处看天"]
    suffix = ["", "", "", "再说", "就好", "不急", "慢慢看"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("opentime")
def _action_opentime(poi, signals):
    if not _pick(signals, "opentime"):
        return None
    close = poi.get("close_hour", 22)
    open_h = poi.get("open_hour", 10)
    if close >= 24:
        opts = ["夜里再去", "等天黑了出发",
                "不急等入夜再去", "深夜去更对味",
                "入夜再去", "等天黑了去",
                "夜里去不亏", "深夜去刚好",
                "等入夜再去", "不急夜里再去",
                "天黑之后再去", "深夜去也不晚",
                "夜里去才对味", "等天黑了出发就好",
                "入夜去更对味", "深夜出发",
                "不急等天黑", "夜里去就好"]
        suffix = ["", "", "", "就好", "再说", "不急"]
        return rng.choice(opts) + rng.choice(suffix)
    if open_h <= 7:
        opts = ["早点去独享", "天亮就出门",
                "清晨去最清净", "赶早不赶晚",
                "一大早去", "清晨去不亏",
                "早起去最好", "天刚亮就去",
                "赶早去独享", "清晨出发",
                "早起去清净", "天亮就出发",
                "一大早去独享", "清晨去刚好",
                "赶早去不亏", "早起出发",
                "天刚亮去最好", "清晨去不挤"]
        suffix = ["", "", "", "就好", "再说", "不急"]
        return rng.choice(opts) + rng.choice(suffix)
    opts = ["进去坐坐", "慢慢待着不赶", "随时去都行",
            "挑个闲的时候去", "不赶时间慢慢待",
            "有空就去不用挑时间", "什么时候去都行",
            "有空就进去坐坐", "不急慢慢待",
            "选个闲的时候去", "随时去随时待",
            "不赶时间慢慢坐", "有空就去坐坐",
            "什么时候去都行", "选个有空的时候去",
            "进去坐坐就好", "慢慢待不赶",
            "有空就去", "不赶时间待着",
            "随便什么时候去", "进去待着",
            "有空去坐坐", "不急慢慢坐",
            "挑个时间慢慢待", "选个时间坐坐",
            "随时去不赶", "有空慢慢待",
            "不赶时间进去坐", "选个闲时去",
            "随时进去坐", "有空不赶时间去"]
    suffix = ["", "", "", "就好", "再说"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("direction")
def _action_direction(poi, signals):
    if not _pick(signals, "direction"):
        return None
    d = poi.get("direction", "")
    opts = [f"往{d}走过去", f"朝着{d}出发", f"跟着{d}风走",
            f"向{d}方向走", f"往{d}边走", f"顺着{d}走",
            f"{d}方出发", f"朝{d}边去"]
    suffix = ["", "", "", "就好", "再说"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("wiki.description")
def _action_wiki(poi, signals):
    if not _pick(signals, "wiki.description"):
        return None
    desc = poi.get("wiki_description", "")
    opts = ["先看看是什么再去", "先了解它再看",
            "先知道是什么再说", "先了解再进去",
            "先看看介绍", "先了解再决定",
            "先看介绍再进去", "先了解再说",
            "先看看是什么", "先了解再去看",
            "先看看介绍再进去", "先了解再去看不亏",
            "先看看是什么再说", "先了解再看",
            "先看介绍再说", "先知道是什么再进去",
            "先了解再进去看", "先看看介绍再说",
            "先了解它再去看", "先看看是什么再决定",
            "先了解再决定去看", "先看介绍再决定",
            "先知道是什么再去", "先看看再进去",
            "先了解它再说", "先看介绍",
            "先了解再去看就好", "先看看是什么再去看",
            "先了解再看再说", "先看看介绍再去看",
            "先了解再去看就对了", "先知道是什么再看"]
    suffix = ["", "", "", "就好", "再说", "不急"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("business_area")
def _action_area(poi, signals):
    if not _pick(signals, "business_area"):
        return None
    area = poi.get("business_area", "")
    opts = [f"到了{area}先走走", f"在{area}一带逛逛",
            f"到了{area}转一圈", f"在{area}附近走走",
            f"去{area}那一带看看", f"到{area}附近逛逛",
            f"在{area}走走看看", f"到{area}转转",
            f"去了{area}先走走", f"在{area}附近转一圈",
            f"到{area}看看再说", f"去{area}那一带逛逛",
            f"到了{area}逛逛", f"在{area}一带走走",
            f"去{area}附近转转", f"到{area}走走看看",
            f"在{area}一带转转", f"去{area}看看",
            f"到了{area}先转一圈", f"在{area}附近逛逛",
            f"去{area}那一带走走", f"到{area}附近走走",
            f"在{area}逛逛", f"去{area}附近看看",
            f"到了{area}走走", f"在{area}转转",
            f"去{area}转一圈", f"到{area}附近转转",
            f"在{area}走走", f"去{area}那一带转转",
            f"到{area}逛逛再说", f"在{area}一带看看",
            f"去了{area}转一圈", f"到{area}附近看看再说"]
    suffix = ["", "", "", "就好", "再说", "不急", "慢慢看"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("business.rating")
def _action_rating(poi, signals):
    if not _pick(signals, "business.rating"):
        return None
    opts = ["放心去不会错", "去了就知道值不值",
            "不用犹豫直接去", "大概率不会踩空",
            "去试试就知道了", "放心去就行",
            "不用纠结去就对了", "去一趟不会白跑",
            "放心去试试", "去了不会后悔",
            "不用犹豫去就好", "去就对了不会错",
            "放心去吧", "去了就懂",
            "不用想太多去就好", "去试试再说",
            "放心去不亏", "去就对了别犹豫",
            "去了就值", "放心去不会白跑",
            "不用犹豫", "去试试不亏",
            "放心去就好", "去一趟试试",
            "不用纠结去试试", "去了就知道",
            "放心去", "去就好",
            "去就对了", "去试试就知道"]
    suffix = ["", "", "", "就好", "再说", "不犹豫", "别犹豫"]
    return rng.choice(opts) + rng.choice(suffix)


@_register_template("business.cost")
def _action_cost(poi, signals):
    if not _pick(signals, "business.cost"):
        return None
    opts = ["不用想太多直接去", "去了不心疼",
            "随便试试不亏", "低成本试试再说",
            "去试试就知道了", "不用计算直接去",
            "去一趟不贵", "随便去不亏",
            "不用犹豫直接去", "试一次再说",
            "去试试不亏", "不用想直接去",
            "随便试试就好", "去了不亏",
            "不用犹豫去就好", "去一趟试试",
            "试一次不亏", "不用计算去就好",
            "随便去试试", "去了就知道值不值",
            "不用想太多去就好", "去试试",
            "随便试试", "去一趟不亏",
            "不用计算试一次", "去就好不亏",
            "直接去试试", "去一趟就好",
            "去了不贵", "随便去试试就好",
            "不用想太多", "去试试就好"]
    suffix = ["", "", "", "就好", "再说", "不犹豫"]
    return rng.choice(opts) + rng.choice(suffix)


ACTION_TEMPLATES = [
    _action_tag, _action_aoi, _action_crowd, _action_sunset,
    _action_photo, _action_heritage, _action_historic, _action_moon,
    _action_opentime, _action_direction, _action_wiki, _action_area,
    _action_rating, _action_cost,
]


# --- Main generation logic --------------------------------------------------

def _try_templates(templates, poi, signals) -> str | None:
    """
    Try templates using weighted random selection.

    Available templates (whose signal source exists) are weighted by
    signal rarity. Instead of always picking the rarest, we sample
    randomly so common signals still get a chance, improving diversity.
    """
    available_sources = {s["source"] for s in signals}

    # Partition into available / unavailable
    available: list[tuple] = []  # (weight, template)
    fallback: list = []
    for tmpl in templates:
        source = TEMPLATE_SOURCE_MAP.get(tmpl, "")
        if source in available_sources:
            weight = SIGNAL_RARITY.get(source, 1)
            available.append((weight, tmpl))
        else:
            fallback.append(tmpl)

    # Try available templates in weighted-random order
    pool = list(available)
    while pool:
        weights = [w for w, _ in pool]
        idx = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        _, tmpl = pool.pop(idx)
        result = tmpl(poi, signals)
        if result:
            return result

    # Fall back to any remaining templates (may work via secondary signals)
    rng.shuffle(fallback)
    for tmpl in fallback:
        result = tmpl(poi, signals)
        if result:
            return result
    return None


def generate_one(poi: dict) -> dict | None:
    """Generate validated copy for one POI from its signals."""
    signals = build_signals(poi)
    if len(signals) < MIN_SIGNALS:
        return None

    for _ in range(8):
        hook = _try_templates(HOOK_TEMPLATES, poi, signals)
        reason = _try_templates(REASON_TEMPLATES, poi, signals)
        oracle = _try_templates(ORACLE_TEMPLATES, poi, signals)
        action = _try_templates(ACTION_TEMPLATES, poi, signals)

        if not all([hook, reason, oracle, action]):
            continue

        copy = {
            "hook": hook.strip(),
            "reason": reason.strip(),
            "oracle": oracle.strip(),
            "action": action.strip(),
        }

        ok, _ = validate_copy(copy, signals)
        if ok:
            entry = {k: copy[k] for k in ("hook", "reason", "oracle", "action")}
            entry["sources"] = sorted({s["source"] for s in signals})
            return {**poi, **entry}

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate copy offline")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    clean_path = Path(CLEAN_POI_FILE)
    if not clean_path.exists():
        print(f"ERROR: {clean_path} not found.", file=sys.stderr)
        return 1

    pois = json.loads(clean_path.read_text(encoding="utf-8"))

    # Balance across categories
    by_cat: dict[str, list[dict]] = {}
    for poi in pois:
        by_cat.setdefault(poi["category"], []).append(poi)
    for bucket in by_cat.values():
        rng.shuffle(bucket)

    selected: list[dict] = []
    quota = max(1, args.limit // max(1, len(by_cat)))
    for bucket in by_cat.values():
        selected.extend(bucket[:quota])
    if len(selected) < args.limit:
        chosen = {p["id"] for p in selected}
        leftovers = [p for p in pois if p["id"] not in chosen]
        rng.shuffle(leftovers)
        selected.extend(leftovers[:args.limit - len(selected)])
    selected = selected[:args.limit]

    print(f"Generating copy for {len(selected)} POIs (offline mode)")

    results: list[dict] = []
    skipped = 0
    for poi in selected:
        record = generate_one(poi)
        if record:
            results.append(record)
        else:
            skipped += 1

    print(f"Generated: {len(results)}, skipped: {skipped}")

    # Report diversity
    from collections import Counter
    print("\n--- copy diversity ---")
    for field in ("hook", "reason", "oracle", "action"):
        values = [r[field] for r in results]
        unique = len(set(values))
        ratio = unique / len(values) if values else 0
        flag = "OK " if ratio >= 0.75 else "LOW"
        print(f"  [{flag}] {field:7s} {unique}/{len(values)} unique ({ratio:.0%})")
        if ratio < 0.75:
            for text, count in Counter(values).most_common(3):
                if count > 1:
                    print(f"         x{count}: {text[:50]}")

    # Write output
    payload = []
    for record in results:
        payload.append({
            "id": record["id"],
            "name": record["name"],
            "category": record["category"],
            "categoryLabel": CATEGORIES[record["category"]]["label"],
            "area": record.get("business_area") or record.get("adname") or "朝阳区",
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

    out = Path(OUTPUT_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nWrote {len(payload)} records -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
