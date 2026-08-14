"""
Validator tests for the copy rules.

`validate_copy` is the last line of defence between a hallucinating model and
the user standing in the wrong place. It has grown alongside the signal layer,
so it needs cases proving it still rejects what it must reject - and, just as
importantly, that it does not reject good copy written from the *new* signals
(heritage, dynasty, AOI, sunset), which would silently starve those categories.

Usage:
    python3 pipeline/verify_copy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.prompts import validate_copy  # noqa: E402

SIGNALS = [
    {"source": "wikidata.inception", "fact": "1530年就在这儿了",
     "angle": "站过一个世纪的东西，不会白看"},
    {"source": "wikidata.heritage", "fact": "这是个被登记在册的地方",
     "angle": "被保下来的东西，总有它的道理"},
    {"source": "aoi", "fact": "它在798艺术区里面",
     "angle": "到了别直奔主目的，周围也算行程"},
    {"source": "sunset", "fact": "今天19:24日落",
     "angle": "提前半小时到，光最好的那段就归你了"},
]


def case(name: str, copy: dict, should_pass: bool, signals=SIGNALS,
         expect: str | None = None) -> bool:
    """
    Assert an outcome, and optionally *why*.

    Checking the reason matters: a case that fails a length check before ever
    reaching the hallucination guard looks green while testing nothing.
    """
    ok, reason = validate_copy(copy, signals)
    good = ok == should_pass
    if good and expect and not ok and expect not in reason:
        good = False
        note = f"rejected for the wrong reason: {reason} (wanted {expect!r})"
    else:
        note = "accepted" if ok else f"rejected ({reason})"
    verdict = "PASS" if good else "FAIL"
    print(f"  [{verdict}] {name}: {note}")
    return good


def main() -> int:
    print("validate_copy cases")
    results = []

    # Good copy written entirely from the new signals. Must be accepted -
    # otherwise the enrichment work buys us nothing.
    results.append(case(
        "grounded copy from new signals",
        {
            "hook": "被登记在册的老地方",
            "reason": "它在艺术区里面，到了别急着直奔一个点，"
                      "周围绕一圈也算在行程里，光最好的那段留给自己。",
            "oracle": "旧的东西自带答案",
            "action": "先绕外围走一圈",
        },
        should_pass=True,
    ))

    # The classic invention we built the guard for.
    results.append(case(
        "invented window seat",
        {
            "hook": "靠窗那个位置是留给你的",
            "reason": "它在艺术区里面，靠窗第三个位置留给你，坐下就不想走了，"
                      "周围绕一圈也算行程。",
            "oracle": "旧的东西自带答案",
            "action": "坐靠窗那一桌",
        },
        should_pass=False,
        expect="ungrounded detail",
    ))

    # Review-site voice, which the product forbids outright.
    results.append(case(
        "review-site voice",
        {
            "hook": "口碑很好的一个地方",
            "reason": "这家店环境优雅服务热情，值得一试，来了就知道为什么"
                      "大家都推荐它，性价比也不错。",
            "oracle": "值得一试的地方",
            "action": "去点招牌菜",
        },
        should_pass=False,
        expect="banned phrase",
    ))

    # A year is real and useful, but a bare digit in `reason` is far more often
    # a leaked rating or price, so the rule stays strict.
    results.append(case(
        "digits in reason",
        {
            "hook": "五百年前就在这儿",
            "reason": "1530年就在这儿了，比这座城市的大部分东西都老，"
                      "走进去的时候慢一点，别急着找什么。",
            "oracle": "旧的东西自带答案",
            "action": "慢慢走一圈",
        },
        should_pass=False,
        expect="digits",
    ))

    # Cats are the model's favourite invention.
    results.append(case(
        "invented cat",
        {
            "hook": "进门左手边趴着一只猫",
            "reason": "它在艺术区里面，进门左手边趴着一只橘猫，见了生人"
                      "也不躲，周围绕一圈再走也不迟。",
            "oracle": "旧的东西自带答案",
            "action": "先绕外围走一圈",
        },
        should_pass=False,
        expect="ungrounded detail",
    ))

    # Exclamation marks break the 《答案之书》 voice.
    results.append(case(
        "exclamation mark",
        {
            "hook": "被登记在册的老地方",
            "reason": "它在艺术区里面，到了别直奔一个点，周围绕一圈也算行程，"
                      "不必急着把它看完！",
            "oracle": "旧的东西自带答案",
            "action": "先绕外围走一圈",
        },
        should_pass=False,
        expect="exclamation",
    ))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} cases behaved as expected")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
