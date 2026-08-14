"""
The copy generation prompt system.

Two principles now govern the copy:

1. 虚实相应 — the fact must be real, only the phrasing is warm.
   The model is given a list of *signals* (verified facts + the angle each
   licenses) and may only write from them. It must never invent a detail.

2. 懂你 — the copy talks *to this user*, using their MBTI / 星座 / 今日偏好 to
   sound like a friend who gets them. This is why generation now happens at
   run time: the same POI reads differently for a 快乐小狗 ESFP and a 深思的
   INTJ, because the copy leans on who is reading it.

Tone shift from the old version:
   * Much less 玄学. No 方位/月相/时辰 fortune-telling ("东南为巽" is gone).
   * Only a *light* touch of luck remains — "今天适合你""会有好事" — because the
     product is still a 扭蛋 and needs a sense of small fortune.
   * Second person throughout ("你"), conversational, like a friend nudging you.
"""

from __future__ import annotations

import json

from pipeline.config import ACTIVE_REGION, CATEGORIES

# ---------------------------------------------------------------------------
# User-persona vocabulary — the "懂你" layer.
# ---------------------------------------------------------------------------
# Each MBTI type gets a popular nickname (the internet's own shorthand) plus a
# couple of traits the copy can lean on. These are used to make the opening
# feel personal ("作为一只 ENFP 快乐小狗，你…"), NOT to state facts about a place.
MBTI_PERSONA: dict[str, dict] = {
    "INTJ": {"nick": "小古板建筑师", "traits": "独立、有主意，喜欢有深度、能一个人琢磨的地方"},
    "INTP": {"nick": "思考型猫头鹰", "traits": "好奇、爱钻研，喜欢有点意思、能满足求知欲的地方"},
    "ENTJ": {"nick": "气场全开指挥官", "traits": "目标感强、效率控，喜欢有格调、不浪费时间的地方"},
    "ENTP": {"nick": "杠精辩论家", "traits": "点子多、爱新鲜，喜欢新奇、能聊能逛的地方"},
    "INFJ": {"nick": "温柔提灯人", "traits": "细腻、有理想，喜欢安静、有故事感的地方"},
    "INFP": {"nick": "梦游小蝴蝶", "traits": "浪漫、内心丰盈，喜欢有氛围、能发呆的地方"},
    "ENFJ": {"nick": "热心小太阳", "traits": "温暖、会照顾人，喜欢能和人待在一起的地方"},
    "ENFP": {"nick": "快乐小狗", "traits": "热情、爱自由，喜欢热闹、能蹦跶能探索的地方"},
    "ISTJ": {"nick": "靠谱老班长", "traits": "务实、守规矩，喜欢稳妥、口碑好的地方"},
    "ISFJ": {"nick": "贴心小棉袄", "traits": "温和、顾家，喜欢舒服、有安全感的地方"},
    "ESTJ": {"nick": "雷厉风行管家", "traits": "干练、讲效率，喜欢成熟、不踩坑的地方"},
    "ESFJ": {"nick": "社交小蜜蜂", "traits": "热心、爱张罗，喜欢有人气、适合聚的地方"},
    "ISTP": {"nick": "酷盖工具人", "traits": "冷静、动手强，喜欢自在、不被打扰的地方"},
    "ISFP": {"nick": "文艺小鹿", "traits": "感性、爱美，喜欢有调调、能慢慢逛的地方"},
    "ESTP": {"nick": "行动派飞人", "traits": "果断、爱刺激，喜欢有活力、说走就走的地方"},
    "ESFP": {"nick": "全场焦点小太阳", "traits": "外向、爱玩，喜欢热闹、能尽兴的地方"},
}

# 星座 — a *very* light touch only. One vibe word per sign, used to colour the
# luck line ("今天水象的你，直觉会准"), never to make claims about the venue.
ZODIAC_VIBE: dict[str, str] = {
    "白羊座": "行动力强、想到就去",
    "金牛座": "务实、会享受",
    "双子座": "好奇、喜欢新鲜",
    "巨蟹座": "顾家、重感觉",
    "狮子座": "爱热闹、想被看见",
    "处女座": "细致、挑得准",
    "天秤座": "爱美、讲氛围",
    "天蝎座": "专注、凭直觉",
    "射手座": "爱自由、爱冒险",
    "摩羯座": "靠谱、有规划",
    "水瓶座": "独特、不爱跟风",
    "双鱼座": "浪漫、爱做梦",
}

# 今日偏好 pills (see App.tsx PREFERENCES) — the mood the user is in today.
PREFERENCE_MOOD: dict[str, str] = {
    "forage": "今天想好好吃一顿",
    "sweat": "今天想动一动、出出汗",
    "stroll": "今天想随便溜达溜达",
    "idle": "今天只想放空、待着",
    "fate": "今天想随缘，交给运气",
}


# --- System prompt ---------------------------------------------------------

# The opening paragraph sets the *scene*, which differs by region: the Beijing
# build is a "城里有一两小时空闲" gacha; the Dolomites build is a mountain-trip
# companion who knows the trails, huts and cable cars. The rules below (虚实相应
# / 懂你 / 浅浅好运感 / 语气) are shared, because they are what make the copy good
# regardless of where the place is.
_SCENE_INTRO = {
    "chaoyang": (
        "你是「下一站扭蛋」的文案作者。这个产品替用户做一个小决策："
        "用户已经出门了，有一两个小时空闲，摇一次扭蛋，你就要像一个很懂 Ta 的朋友，"
        "给出一个\"现在就去做\"的建议。"
    ),
    "dolomites": (
        "你是「下一站扭蛋」的文案作者，这一站在意大利的多洛米蒂山区（Dolomiti，"
        "阿尔卑斯的白云石山群，世界自然遗产）。用户正在这片山里旅行，摇一次扭蛋，"
        "你就要像一个走遍了这些山谷、住过山间小屋、坐过每条缆车的朋友，给 Ta 一个"
        "\"现在就去\"的建议——可能是爬到某个 rifugio 喝碗热汤，绕一汪高山湖走一圈，"
        "或坐缆车上到能看见三座山峰的垭口。"
        "记住这里是山：会累、会冷、会有海拔，好风景都要走一段才有——别把它写成逛街。"
    ),
}

SYSTEM_PROMPT = _SCENE_INTRO.get(ACTIVE_REGION, _SCENE_INTRO["chaoyang"]) + """

## 第一原则：虚实相应（事实必须真）

我会给你一组【信号】，每条包含 fact（关于这个地点的真实事实，来自地图数据）\
和 angle（这条事实允许的表达角度）。

规则：
1. 你写的每一个关于地点的具体信息，都必须能在某条 fact 里找到出处
2. 信号里没有的地点细节，一个字都不许编（用户会真的走过去，编了产品就废了）
3. 反面禁止：评分数字、人均价格、"环境优雅/服务热情/网红/必去"这类大众点评式套话

## 第二原则：懂你（对着这个用户说话）

我会告诉你用户的 MBTI 昵称与特质、星座、今天的心情偏好。你要让文案听起来\
像"你太懂我了"：
- 多用「你」，像朋友对你说话，别用"这家店""该店"这种第三人称
- 可以顺着用户的性格特质推荐（例：对「快乐小狗」ENFP 说"这种热闹地儿就是为你开的"；\
对喜欢独处的 I 人说"正好一个人待着，没人打扰你"）
- 把用户"今天的心情"接住（例：偏好是"放空"，就别催 Ta 赶时间）
- 性格/星座是用来"拉近关系、解释为什么推荐给你"的，不是用来编造地点事实的

## 第三原则：浅浅的好运感（不是玄学天书）

这是个扭蛋，保留一点点"手气/好事发生"的仪式感，但要克制：
- 可以说"今天你手气不错""会有点小惊喜""适合你"这种轻盈的好运表达
- 禁止方位玄学、月相、时辰、五行那套（不要"东南为巽""日落前一小时"这种天书）
- 好运感一句话点到为止，重点还是"为什么这个地方适合此刻的你"

## 语气
像一个懂你的朋友随口一说：口语、亲切、笃定、简短。不堆形容词，不喊口号，不用感叹号。

## 输出格式
严格输出 JSON，不要解释文字，不要 markdown 代码块。"""


# --- Field specs -----------------------------------------------------------

FIELD_SPEC = {
    "hook": "12-22字。一句话钩子，结合用户性格/心情切入，让人觉得'这是给我的'。可含地点事实。",
    "reason": "35-60字。核心理由。用上至少2条信号的 fact，串成有画面的一段话，多用「你」。",
    "oracle": "10-20字。一句浅浅的好运/鼓励话，像朋友拍拍你说'去吧'。可含轻微好运感，但不要方位月相时辰。",
    "action": "6-14字。一个此刻就能做的具体动作，必须基于信号（如某道招牌菜、某件能做的事）。",
}


def _persona_block(profile: dict | None) -> list[str]:
    """Render the user's persona into prompt lines. Empty when no profile."""
    if not profile:
        return []
    lines: list[str] = []
    mbti = (profile.get("mbti") or "").upper()
    persona = MBTI_PERSONA.get(mbti)
    if persona:
        lines.append(f"- MBTI：{mbti}（{persona['nick']}）—— {persona['traits']}")
    zodiac = profile.get("zodiac") or ""
    vibe = ZODIAC_VIBE.get(zodiac)
    if vibe:
        lines.append(f"- 星座：{zodiac}（{vibe}）")
    prefs = profile.get("preferences") or []
    moods = [PREFERENCE_MOOD[p] for p in prefs if p in PREFERENCE_MOOD]
    if moods:
        lines.append(f"- 今天的心情：{'；'.join(moods)}")
    if not lines:
        return []
    return ["", "【这个用户是谁】（用来拉近关系、解释为什么推荐给 Ta，不是地点事实）", *lines]


def build_user_prompt(poi: dict, signals: list[dict],
                      profile: dict | None = None) -> str:
    """
    Build the per-POI prompt from verified signals and the user's persona.

    `profile` carries the run-time user context: {mbti, zodiac, preferences}.
    When absent, the copy is written generically (still warm, just not tailored).
    """
    category = poi.get("category", "")
    # Fall back to any bucket that exists in the active region (the Beijing
    # build has "cafe"; the Dolomites build does not), so a stray/unknown
    # category never raises a KeyError.
    _fallback = next(iter(CATEGORIES.values()))
    meta = CATEGORIES.get(category, _fallback)
    # Prefer the cleaned, brand-only name so the copy never says "…东坝店463".
    name = poi.get("display_name") or poi.get("name", "")

    lines = [
        f"地点名称：{name}",
        f"品类：{meta['label']}（{meta['vibe']}）",
        "",
        "【可用信号】（你只能用这些事实，不许编造其他地点信息）",
    ]
    for index, signal in enumerate(signals, start=1):
        lines.append(f"{index}. fact: {signal['fact']}")
        lines.append(f"   angle: {signal['angle']}")

    lines += _persona_block(profile)

    if len(signals) <= 2:
        lines.append("")
        lines.append("注意：这个地点的可用信息很少。宁可写得克制、留白，"
                     "也不要为了凑字数编造细节。短而真 > 长而假。")

    return (
        "\n".join(lines)
        + "\n\n请输出如下 JSON：\n"
        + json.dumps(FIELD_SPEC, ensure_ascii=False, indent=2)
    )


# --- Validation ------------------------------------------------------------

# Dianping-style marketing speak the product refuses. (Fortune words like
# 幸运/好事 are now allowed — see the tone rules — so they are NOT banned.)
BANNED_SUBSTRINGS = [
    "评分", "人均", "性价比", "口碑好", "网红", "打卡地", "必去", "必吃",
    "物超所值", "服务热情", "环境优雅", "值得一试", "强烈推荐",
    "这家店", "该店", "本店", "米其林", "第一名", "排名",
    # Retired 玄学 vocabulary — we no longer want fortune-telling jargon.
    "东南为巽", "五行", "月相", "上弦月", "下弦月", "峨眉月", "盈凸月",
]

LENGTH_LIMITS = {
    "hook": (8, 28),
    "reason": (25, 75),
    "oracle": (6, 26),
    "action": (4, 20),
}

# Concrete details the model likes to invent. Allowed only when a signal
# actually mentions them.
HALLUCINATION_MARKERS = [
    "靠窗", "窗边", "第二个位置", "第三个", "老板", "店主", "猫", "狗",
    "阳光从", "夕阳从", "西晒", "二楼", "阁楼", "露台", "后院",
]


def validate_copy(copy: dict, signals: list[dict] | None = None,
                  profile: dict | None = None) -> tuple[bool, str]:
    """
    Check generated copy against the product's rules.

    When `signals` is supplied we additionally check that suspiciously concrete
    details are grounded in the data. `profile` is accepted so callers can pass
    it uniformly; persona words (the MBTI nickname) are expected in the copy and
    are never treated as hallucinations.
    """
    for field in FIELD_SPEC:
        value = copy.get(field)
        if not isinstance(value, str) or not value.strip():
            return False, f"missing field: {field}"
        text = value.strip()
        low, high = LENGTH_LIMITS[field]
        if not (low <= len(text) <= high):
            return False, f"{field} length {len(text)} outside {low}-{high}"
        for banned in BANNED_SUBSTRINGS:
            if banned in text:
                return False, f"{field} contains banned phrase: {banned}"
        if "！" in text or "!" in text:
            return False, f"{field} uses exclamation mark"
        # Digits usually mean a leaked rating or price. hook may carry a
        # persona token, but numbers there are still unwanted.
        if any(ch.isdigit() for ch in text):
            return False, f"{field} contains digits (likely rating/price leak)"

    if signals:
        # Persona words are legitimately absent from the signals, so exclude
        # them from the hallucination corpus check by only scanning for the
        # invented *place* details in HALLUCINATION_MARKERS.
        corpus = " ".join(f"{s['fact']} {s['angle']}" for s in signals)
        blob = " ".join(str(copy.get(f, "")) for f in FIELD_SPEC)
        for marker in HALLUCINATION_MARKERS:
            if marker in blob and marker not in corpus:
                return False, f"ungrounded detail: {marker}"
    return True, "ok"
