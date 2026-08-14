"""
Test helper - synthesise raw POIs shaped exactly like AMap's response.

This exists so the clean/generate stages can be exercised (and the app can be
developed) before anyone spends AMap or OpenAI quota. It is NOT a data source:
the names are obviously synthetic and must never ship to users.

Usage:
    python3 pipeline/mock_poi.py --count 1400
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import CATEGORIES, DISTRICT_BBOX, RAW_POI_FILE  # noqa: E402

AREAS = ["三里屯", "望京", "国贸", "双井", "亮马桥", "团结湖", "朝阳公园",
         "酒仙桥", "百子湾", "东坝", "青年路", "常营", "十里堡", "潘家园"]

# Mimics AMap's real `business.tag` (comma-separated dish/feature keywords,
# food POIs only) and photo titles, so the signal layer can be exercised.
TAGS = {
    "cafe": ["手冲,肉桂卷,燕麦拿铁", "挂耳,芝士蛋糕", "冷萃,可颂", ""],
    "food": ["烤鸭,爆肚,豆汁儿", "麻辣香锅,酸梅汤", "炸酱面,炒肝", "羊蝎子,烧饼"],
    "park": [""],
    "culture": [""],
    "shop": [""],
    "night": ["精酿,现场演出", ""],
    "weird": [""],
}
PHOTO_TITLES = ["环境", "门头", "内景", "菜品", "", "图片"]

PARTS = {
    "cafe": (["晴", "野", "拾", "叁", "白", "松", "屿", "浮", "青", "南"],
             ["咖啡", "咖啡馆", "烘焙室", "茶室", "café", "手冲店"]),
    "food": (["老", "小", "巷", "南门", "胡同", "阿", "山", "河", "食", "灶"],
             ["面馆", "小馆", "食堂", "馆子", "菜馆", "厨房", "夜宵铺"]),
    "park": (["朝阳", "红领巾", "庆丰", "将府", "常营", "太阳宫", "望和"],
             ["公园", "绿地", "郊野公园", "滨河步道", "城市森林"]),
    "culture": (["无用", "白塔", "汇观", "尤伦斯", "798", "木木", "时代"],
                ["美术馆", "书店", "博物馆", "艺术中心", "图书馆"]),
    "shop": (["旧", "拾光", "半", "叁号", "复古", "巷子", "杂"],
             ["买手店", "古着店", "杂货铺", "市集", "选物店"]),
    "night": (["夜", "潜", "海", "回声", "地下", "临界", "月"],
              ["livehouse", "酒馆", "小酒吧", "观景台", "顶楼"]),
    "weird": (["无名", "第七", "废墟", "天台", "拐角", "旧厂", "尽头"],
              ["空间", "角落", "改造区", "小院", "工作室"]),
}


def build_mock_pois(count: int = 1400) -> list[dict]:
    """
    Generate `count` synthetic POIs in AMap's response shape.

    Exposed as a function (not just a CLI) so other tooling - notably
    verify_signals.py - can exercise the signal layer without touching disk
    or spending quota.
    """
    lng_min, lat_min, lng_max, lat_max = DISTRICT_BBOX
    categories = list(CATEGORIES)
    pois = []
    used: set[str] = set()

    index = 0
    while len(pois) < count:
        index += 1
        category = categories[index % len(categories)]
        prefixes, suffixes = PARTS[category]
        name = (f"{random.choice(prefixes)}{random.choice(suffixes)}"
                f"·{random.choice(AREAS)}店{index}")
        if name in used:
            continue
        used.add(name)
        open_h, close_h = CATEGORIES[category]["default_open"]
        # Only food-ish categories get rating/cost/tag, mirroring AMap's
        # documented behaviour (those fields are category-restricted).
        has_business = category in ("cafe", "food", "night")
        photo_n = random.randint(0, 4)
        pois.append({
            "id": f"MOCK{index:06d}",
            "name": name,
            "category": category,
            "amap_type": CATEGORIES[category]["label"],
            "typecode": CATEGORIES[category]["amap_types"].split("|")[0],
            "address": f"朝阳区{random.choice(AREAS)}路{random.randint(1, 200)}号",
            "adname": "朝阳区",
            "business_area": random.choice(AREAS),
            "lng": round(random.uniform(lng_min, lng_max), 6),
            "lat": round(random.uniform(lat_min, lat_max), 6),
            "tel": "",
            "opentime": f"{open_h:02d}:00-{min(close_h, 23):02d}:00",
        "rating": (str(round(random.uniform(3.6, 4.9), 1))
                   if has_business and random.random() < 0.75 else ""),
        "cost": (str(random.choice([25, 45, 60, 90, 150, 260]))
                 if has_business and random.random() < 0.6 else ""),
        "tag": (random.choice(TAGS[category])
                if has_business and random.random() < 0.7 else ""),
        "alias": "",
        "photo_titles": [t for t in random.sample(PHOTO_TITLES, photo_n) if t],
        "photo_count": photo_n,
        })
    return pois


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1400)
    args = parser.parse_args()

    pois = build_mock_pois(args.count)

    out = Path(RAW_POI_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pois, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(pois)} MOCK POIs -> {out}")
    print("NOTE: mock data is for pipeline testing only, never ship it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
