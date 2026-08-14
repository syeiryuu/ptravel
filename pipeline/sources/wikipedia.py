"""
Chinese Wikipedia + Wikidata client - the *history* half of our real-data story.

Why this source
---------------
Our weakest categories are exactly the ones AMap tells us least about:
  park    - no dish tags, often no rating, no photos worth naming
  culture - a bookstore's `tag` field is usually empty
  weird   - by definition off the commercial grid

But these are precisely the places that *have history*: a park has a founding
year, a temple has a dynasty, a museum has a subject. Wikipedia gives us that
for free, with no scraping and no terms-of-service grey area:
  * the REST summary endpoint is public and explicitly meant for reuse
  * content is CC BY-SA, and we never reproduce it verbatim - we extract
    structured facts (year, type) and write our own copy from them

An inception year is a fantastic gacha signal: "1958年就在这儿了" is verifiable,
concrete, and lands as mystical without any embellishment.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://zh.wikipedia.org/w/api.php"
SUMMARY_URL = "https://zh.wikipedia.org/api/rest_v1/page/summary/"
WIKIDATA_URL = "https://www.wikidata.org/w/api.php"

# zh.wikipedia serves whichever variant the article was written in, so a
# lookup can come back in Traditional Chinese ("北京市朝陽區的藝術園區").
# Shipping that into copy aimed at Beijing users would look broken, and the
# `variant=zh-cn` parameter is only honoured intermittently - so we normalise
# the handful of characters that actually show up in place descriptions.
_TRAD_TO_SIMP = str.maketrans({
    "陽": "阳", "區": "区", "園": "园", "藝": "艺", "術": "术", "陳": "陈",
    "館": "馆", "廣": "广", "場": "场", "國": "国", "體": "体", "與": "与",
    "樓": "楼", "臺": "台", "鐵": "铁", "遷": "迁", "庫": "库", "廠": "厂",
    "橋": "桥", "門": "门", "長": "长", "點": "点", "畫": "画", "東": "东",
    "樂": "乐", "團": "团", "匯": "汇", "總": "总", "處": "处", "紀": "纪",
    "畢": "毕", "葉": "叶", "聲": "声", "漢": "汉", "記": "记", "寫": "写",
    "屬": "属", "設": "设", "為": "为", "於": "于",
    "個": "个", "們": "们", "來": "来", "後": "后", "萬": "万", "當": "当",
})


def _simplify(text: str | None) -> str | None:
    """Best-effort Traditional -> Simplified for short descriptions."""
    if not text:
        return text
    return text.translate(_TRAD_TO_SIMP)

# Wikimedia requires a descriptive UA with contact info; anonymous scrapers
# get throttled or blocked.
USER_AGENT = (
    "LuckyGacha/1.0 (personal side project; contact via github) "
    "python-urllib"
)

# Wikidata property ids we care about.
P_INCEPTION = "P571"       # 成立/建立时间
P_HERITAGE = "P1435"       # 文物保护单位级别
P_ARCHITECT = "P84"        # 建筑师
P_ARCHITECTURAL_STYLE = "P149"

_REQUEST_INTERVAL = 0.2
_last_request = 0.0


def _throttled_get(url: str) -> dict | None:
    """Polite GET: one request per 200ms, descriptive UA, silent on failure."""
    global _last_request
    elapsed = time.time() - _last_request
    if elapsed < _REQUEST_INTERVAL:
        time.sleep(_REQUEST_INTERVAL - elapsed)
    _last_request = time.time()

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:      # no article - the common, expected case
            return None
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def _normalise_name(name: str) -> str:
    """
    Strip branch suffixes so "单向空间(朝阳大悦城店)" can match "单向空间".

    Without this almost nothing matches: AMap names are operational, Wikipedia
    titles are canonical.
    """
    name = re.sub(r"[（(].*?[)）]", "", name)
    for suffix in ("店", "分店", "旗舰店", "总店", "北京店"):
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            name = name[: -len(suffix)]
    return name.strip()


# Generic tails that a Wikipedia title may legitimately drop:
# the article for 「日坛公园」 is simply 「日坛」.
_DROPPABLE_TAILS = ("公园", "公園", "博物馆", "美术馆", "艺术区",
                    "书店", "广场", "公墓", "体育馆", "体育场")

# Titles that are never a specific venue. Matching one of these means we have
# drifted from "this place" to "the district it sits in", which would attach
# a whole district's history to one small shop.
_TOO_GENERIC = {"北京", "北京市", "朝阳区", "朝陽區", "中国", "北京市朝阳区"}


def _titles_match(query: str, title: str) -> bool:
    """
    Decide whether a search hit really refers to the place we asked about.

    Strictness matters more than recall here: a missed match costs one signal,
    a wrong match ships a confident falsehood.
    """
    if title in _TOO_GENERIC:
        return False
    if query == title:
        return True
    # "朝阳公园" vs "朝阳公园 (北京)" - a disambiguated variant of the same name.
    base = re.sub(r"\s*[（(].*?[)）]\s*$", "", title).strip()
    if base == query:
        return True
    # "日坛公园" vs "日坛" - the article drops a generic tail.
    for tail in _DROPPABLE_TAILS:
        if query.endswith(tail) and query[: -len(tail)].strip() == base:
            return True
    # "北京中国紫檀博物馆" vs "中国紫檀博物馆" - a dropped city prefix.
    if len(base) >= 4 and (query.endswith(base) or base.endswith(query)):
        return True
    return False


def search_titles(name: str, limit: int = 5) -> list[str]:
    """
    Plausible article titles for this place, best first.

    Returns a list rather than one title so `lookup` can step past a
    disambiguation page to the real article underneath it.
    """
    query = _normalise_name(name)
    if len(query) < 2:
        return []
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
        "utf8": 1,
    }
    payload = _throttled_get(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}")
    if not payload:
        return []
    hits = [h.get("title", "") for h in
            ((payload.get("query") or {}).get("search") or [])]
    return [t for t in hits if _titles_match(query, t)]


def fetch_summary(title: str) -> dict | None:
    """
    Article summary. We keep only what we can turn into a signal - never the
    prose itself, which stays on Wikipedia where its licence lives.
    """
    payload = _throttled_get(SUMMARY_URL + urllib.parse.quote(title))
    if not payload:
        return None
    if payload.get("type") == "disambiguation":
        return None
    return {
        "title": payload.get("title"),
        "description": _simplify(payload.get("description")),
        "extract": _simplify(payload.get("extract", "")[:400]),
        "wikidata_id": ((payload.get("wikibase_item"))
                        or (payload.get("titles") or {}).get("canonical")),
        "url": ((payload.get("content_urls") or {}).get("desktop") or {}).get("page"),
    }


def _claim_year(claims: dict, prop: str) -> str | None:
    entries = claims.get(prop) or []
    for entry in entries:
        value = (((entry.get("mainsnak") or {}).get("datavalue") or {})
                 .get("value") or {})
        stamp = value.get("time") if isinstance(value, dict) else None
        if stamp:
            match = re.search(r"(\d{4})", stamp)
            if match and match.group(1) != "0000":
                return match.group(1)
    return None


def fetch_wikidata(entity_id: str) -> dict | None:
    """Structured facts, which are far safer to reuse than free text."""
    if not entity_id or not entity_id.startswith("Q"):
        return None
    params = {
        "action": "wbgetentities",
        "ids": entity_id,
        "props": "claims",
        "format": "json",
    }
    payload = _throttled_get(f"{WIKIDATA_URL}?{urllib.parse.urlencode(params)}")
    if not payload:
        return None
    entity = (payload.get("entities") or {}).get(entity_id) or {}
    claims = entity.get("claims") or {}
    return {
        "inception": _claim_year(claims, P_INCEPTION),
        "is_heritage": bool(claims.get(P_HERITAGE)),
        "has_architect": bool(claims.get(P_ARCHITECT)),
    }


# Article prose we can mine for a founding year when Wikidata has none.
_YEAR_PATTERNS = [
    re.compile(r"(?:始建于|建于|创建于|落成于|成立于|开放于|开馆于)\s*(\d{3,4})\s*年"),
    re.compile(r"(\d{3,4})\s*年\s*(?:建成|开放|开馆|落成|创立|成立)"),
]

# Heritage status is frequently stated in the article text but missing from
# Wikidata's P1435 claim, so the prose is the more reliable source here.
_HERITAGE_PATTERNS = (
    "全国重点文物保护单位", "市级文物保护单位",
    "区级文物保护单位", "文物保护单位", "世界文化遗产",
)

# Dynasty mentions are a strong, checkable "this is old" marker for places
# whose exact founding year nobody recorded.
_DYNASTY_PATTERN = re.compile(
    r"(明朝|清朝|元朝|宋朝|唐朝|明代|清代|元代|明清)"
)


def extract_heritage(extract: str | None) -> bool:
    if not extract:
        return False
    return any(pattern in extract for pattern in _HERITAGE_PATTERNS)


def extract_dynasty(extract: str | None) -> str | None:
    if not extract:
        return None
    match = _DYNASTY_PATTERN.search(extract)
    return match.group(1) if match else None


def extract_inception(extract: str | None) -> str | None:
    """Fallback year extraction from the summary text."""
    if not extract:
        return None
    for pattern in _YEAR_PATTERNS:
        match = pattern.search(extract)
        if match:
            year = match.group(1)
            if 1000 <= int(year) <= 2030:
                return year
    return None


def lookup(name: str) -> dict | None:
    """
    Full lookup for one POI name. Returns None when there is no confident match.

    Being strict here is deliberate: a wrong match is worse than no match,
    because it produces copy that is confidently false.
    """
    # Walk the candidates rather than betting on the first: the top hit for
    # 「朝阳公园」 is a disambiguation page, and the article we actually want
    # (「朝阳公园 (北京)」) is the one below it.
    candidates = search_titles(name)
    summary = None
    for candidate in candidates:
        summary = fetch_summary(candidate)
        if summary:
            break
    if not summary and candidates:
        # Every candidate was a disambiguation page. Try the Beijing-qualified
        # variant, which is the convention zh.wikipedia uses for place names
        # that collide across cities (「日坛」 -> 「日坛 (北京)」).
        for candidate in candidates:
            summary = fetch_summary(f"{candidate} (北京)")
            if summary:
                break
    if not summary:
        return None

    result = {
        "wiki_title": summary["title"],
        "wiki_description": summary.get("description"),
        "wiki_url": summary.get("url"),
    }

    extract = summary.get("extract")

    inception = None
    is_heritage = False
    entity_id = summary.get("wikidata_id")
    if entity_id and isinstance(entity_id, str) and entity_id.startswith("Q"):
        facts = fetch_wikidata(entity_id)
        if facts:
            inception = facts.get("inception")
            is_heritage = facts.get("is_heritage", False)

    # Fall back to the prose for both facts - Wikidata's coverage of Chinese
    # heritage listings is patchy, but the articles state it plainly.
    if not inception:
        inception = extract_inception(extract)
    if not is_heritage:
        is_heritage = extract_heritage(extract)

    if inception:
        result["inception"] = inception
    if is_heritage:
        result["is_heritage"] = True
    dynasty = extract_dynasty(extract)
    if dynasty and not inception:
        result["dynasty"] = dynasty

    # A title alone adds nothing; require at least one usable fact.
    if not any(result.get(k) for k in
               ("inception", "is_heritage", "dynasty", "wiki_description")):
        return None
    return result
