"""
Shared configuration for the 幸运扭蛋 data pipeline.

Design notes
------------
POI category codes are taken from AMap's official
"高德POI分类与编码 V1.06" table, not guessed. Each product-level bucket maps to
one or more AMap mid-level codes.

The seven buckets mirror the product's category-rotation rule: when a user
rejects a suggestion we must hand them a *different* bucket, so buckets need to
feel genuinely distinct to a human, not just different codes.
"""

from __future__ import annotations

import os

# --- Active region switch --------------------------------------------------
# The pipeline can target more than one place. Which one is live is chosen by
# the PTRAVEL_REGION env var (default: the original Beijing 朝阳区 build), so
# every downstream module keeps importing CATEGORIES / DISTRICT_BBOX unchanged
# and simply sees the active region's values.
#
#   export PTRAVEL_REGION=chaoyang    # 北京朝阳区（高德数据源）
#   export PTRAVEL_REGION=dolomites   # 意大利多洛米蒂（OpenStreetMap 数据源）
ACTIVE_REGION = os.environ.get("PTRAVEL_REGION", "chaoyang").strip().lower()

# --- Target area (Beijing 朝阳区) ------------------------------------------
# 朝阳区, Beijing. adcode is stable and preferred over free-text city names.
CITY_NAME = "北京市"
DISTRICT_NAME = "朝阳区"
DISTRICT_ADCODE = "110105"

# Bounding box for 朝阳区 (lng_min, lat_min, lng_max, lat_max).
# Used for grid slicing, since AMap caps any single query at 100 pages.
DISTRICT_BBOX = (116.3400, 39.8300, 116.6400, 40.0500)

# --- Category buckets (Beijing 朝阳区, AMap source) ------------------------
# duration_minutes: how long the activity realistically fills. The product
# promises "30 minutes to 5 hours", so anything outside that is dropped.
CATEGORIES: dict[str, dict] = {
    "cafe": {
        "label": "咖啡馆 / 茶室",
        "amap_types": "050500|050600|050900",
        "duration_minutes": (40, 120),
        "default_open": (8, 22),
        "vibe": "坐下来发会儿呆、看看人、喝一杯的地方",
    },
    "food": {
        "label": "餐馆 / 小吃 / 深夜食堂",
        "amap_types": "050100|050200|050300|050400",
        "duration_minutes": (45, 120),
        "default_open": (10, 22),
        "vibe": "吃一顿，不用正襟危坐的那种",
    },
    "park": {
        "label": "公园 / 步道 / 城市绿地",
        "amap_types": "110100|110200",
        "duration_minutes": (40, 180),
        "default_open": (6, 21),
        "vibe": "走一走、晒晒太阳、什么都不干也行",
    },
    "culture": {
        "label": "博物馆 / 美术馆 / 书店",
        "amap_types": "140100|140200|140400|140500|061205",
        "duration_minutes": (60, 180),
        "default_open": (9, 18),
        "vibe": "安静地看点东西，脑子被填满一点",
    },
    "shop": {
        "label": "买手店 / 市集 / 古着店",
        "amap_types": "061000|061201|060500",
        "duration_minutes": (40, 120),
        "default_open": (10, 21),
        "vibe": "逛，不一定买，看见有意思的东西就够了",
    },
    "night": {
        "label": "livehouse / 酒吧 / 观景点",
        "amap_types": "080304|080600|080300",
        "duration_minutes": (60, 180),
        "default_open": (18, 26),  # 26 == 次日 02:00
        "vibe": "天黑之后才成立的事",
    },
    "weird": {
        "label": "小众怪地方",
        "amap_types": "110000|140800|080500",
        "duration_minutes": (30, 120),
        "default_open": (9, 21),
        "vibe": "说不清算什么，但去了会记住的地方",
    },
}

# ---------------------------------------------------------------------------
# 意大利多洛米蒂（Dolomites）—— OpenStreetMap 数据源
# ---------------------------------------------------------------------------
# A different place needs a different vocabulary: there are no 深夜食堂 or
# livehouse in the Alps, but there are rifugi (mountain huts), high lakes,
# passes, cable cars and via ferrata. We keep exactly the same *shape* as the
# Beijing buckets so clean/signals/prompts work unchanged; only the meaning of
# each bucket and its OSM tag selectors differ.
#
# `osm` is a list of Overpass tag selectors (each a dict of key->value, where a
# value of True means "tag present with any value"). The collector ORs them.
DOLOMITES_CATEGORIES: dict[str, dict] = {
    "hut": {
        "label": "山间小屋 / 牧场餐厅（rifugio·malga）",
        "osm": [
            {"tourism": "alpine_hut"},
            {"tourism": "wilderness_hut"},
            {"amenity": "restaurant", "cuisine": "regional"},
        ],
        "duration_minutes": (45, 150),
        "default_open": (8, 18),
        "vibe": "爬到半山，坐下来喝碗热汤、吃盘手工意面的地方",
    },
    "peak": {
        "label": "山峰 / 垭口 / 观景台",
        "osm": [
            {"natural": "peak"},
            {"mountain_pass": "yes"},
            {"tourism": "viewpoint"},
        ],
        "duration_minutes": (40, 180),
        "default_open": (0, 24),
        "vibe": "站上去，把整片白云石山群装进眼睛里",
    },
    "lake": {
        "label": "高山湖泊 / 水边",
        "osm": [
            {"natural": "water", "water": "lake"},
            {"natural": "water", "water": "reservoir"},
        ],
        "duration_minutes": (40, 150),
        "default_open": (0, 24),
        "vibe": "绕着一汪碧绿的湖水走一圈，看倒影",
    },
    "trail": {
        "label": "徒步路线 / 瀑布 / 峡谷",
        "osm": [
            {"tourism": "attraction", "natural": "waterfall"},
            {"waterway": "waterfall"},
            {"natural": "gorge"},
            {"tourism": "attraction"},
        ],
        "duration_minutes": (60, 240),
        "default_open": (0, 24),
        "vibe": "沿着一条真正的山路走进去，风景自己会说话",
    },
    "cable": {
        "label": "缆车 / 索道 / 高山站台",
        "osm": [
            {"aerialway": "cable_car"},
            {"aerialway": "gondola"},
            {"aerialway": "station"},
        ],
        "duration_minutes": (30, 90),
        "default_open": (8, 18),
        "vibe": "不想爬也没关系，坐上去让缆车替你抬升一千米",
    },
    "village": {
        "label": "山城 / 教堂 / 小博物馆",
        "osm": [
            {"tourism": "museum"},
            {"historic": "castle"},
            {"amenity": "place_of_worship"},
            {"place": "village"},
        ],
        "duration_minutes": (40, 150),
        "default_open": (9, 18),
        "vibe": "在山谷里的小镇慢慢逛，看木屋、尖顶和石头墙",
    },
    "food": {
        "label": "山谷餐厅 / 咖啡 / 甜点",
        "osm": [
            {"amenity": "restaurant"},
            {"amenity": "cafe"},
            {"shop": "pastry"},
        ],
        "duration_minutes": (45, 120),
        "default_open": (8, 22),
        "vibe": "坐下来，尝一口南蒂罗尔的苹果卷和一杯浓缩",
    },
}

# Dolomites core bbox (lng_min, lat_min, lng_max, lat_max) — covers Val Gardena,
# Alta Badia, Cortina d'Ampezzo, Val di Fassa and the passes between them.
DOLOMITES_BBOX = (11.55, 46.30, 12.25, 46.75)
DOLOMITES_CITY_NAME = "Dolomiti"
DOLOMITES_DISTRICT_NAME = "Dolomiti"

# --- Region resolution -----------------------------------------------------
# Overwrite the module-level "active" values from the chosen region. Downstream
# code keeps importing CATEGORIES / DISTRICT_BBOX and transparently gets the
# right place. DATA_SOURCE tells the collector which backend to hit.
DATA_SOURCE = "amap"  # default region's source

if ACTIVE_REGION == "dolomites":
    CATEGORIES = DOLOMITES_CATEGORIES
    DISTRICT_BBOX = DOLOMITES_BBOX
    CITY_NAME = DOLOMITES_CITY_NAME
    DISTRICT_NAME = DOLOMITES_DISTRICT_NAME
    DISTRICT_ADCODE = ""
    DATA_SOURCE = "osm"

# --- Hard exclusions -------------------------------------------------------
# The product explicitly refuses "heavy" suggestions ("去故宫逛一天").
# Anything matching these is dropped regardless of category.
HEAVY_NAME_PATTERNS = [
    "欢乐谷", "游乐园", "主题公园", "动物园", "海洋馆", "水上乐园",
    "度假区", "会展中心", "国家会议中心", "机场", "火车站", "客运站",
    "批发市场", "建材", "汽车", "4S", "医院", "诊所", "药房",
    "银行", "支行", "营业厅", "政务", "派出所", "学校", "大学",
    "培训", "驾校", "写字楼", "产业园", "科技园", "公司", "办公",
]

# Chains produce identical, soulless copy - the opposite of the product's soul.
CHAIN_NAME_PATTERNS = [
    "星巴克", "瑞幸", "麦当劳", "肯德基", "必胜客", "萨莉亚",
    "海底捞", "西贝", "真功夫", "永和大王", "吉野家", "COSTA",
    "全家", "7-ELEVEn", "便利蜂", "罗森", "沃尔玛", "家乐福",
    "华联", "物美", "永辉", "屈臣氏", "万宁",
]

# --- Lucky mechanics -------------------------------------------------------
# Rarity tiers drive both the copy tone and the gacha's sense of ceremony.
RARITY_WEIGHTS = {
    "common": 0.72,   # 日常小事
    "uncommon": 0.22,  # 有点意思
    "rare": 0.06,      # 稀有扭蛋：特定条件才成立的好事
}

# 玄学方位, used by the copy to make it feel like fate rather than an algorithm.
DIRECTIONS = ["正东", "东南", "正南", "西南", "正西", "西北", "正北", "东北"]

# --- Paths -----------------------------------------------------------------
# Each region keeps its own files so a Dolomites build never overwrites or
# pollutes the Beijing one (and vice-versa). The default region keeps the
# original, un-suffixed names for backward compatibility.
_SUFFIX = "" if ACTIVE_REGION == "chaoyang" else f"_{ACTIVE_REGION}"

RAW_POI_FILE = f"pipeline/data/raw_poi{_SUFFIX}.json"
CLEAN_POI_FILE = f"pipeline/data/clean_poi{_SUFFIX}.json"
OUTPUT_FILE = f"public/data/gacha{_SUFFIX}.json"
CACHE_FILE = f"pipeline/data/copy_cache{_SUFFIX}.json"
# Self-owned POI database. Every fetch upserts here so we never re-pay for a POI
# we already have, and clean/copy/recommend all read from one source.
DB_FILE = f"pipeline/data/ptravel{_SUFFIX}.db"
DB_SCHEMA_FILE = "pipeline/db/schema.sql"
# Extra facts fetched from Wikipedia / regeo / QWeather, cached by POI id so
# reruns cost no third-party quota.
ENRICH_CACHE_FILE = f"pipeline/data/enrich_cache{_SUFFIX}.json"
