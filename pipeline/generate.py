"""
Step 4 - Generate the copy for every cleaned POI via the OpenAI API.

Features that matter at 1000-record scale:
  * on-disk cache keyed by POI id, so reruns cost nothing and crashes resume
  * bounded concurrency
  * validation against the product's voice rules, with regeneration on failure
  * graceful degradation: a POI that keeps failing is dropped, never shipped broken

Usage:
    export OPENAI_API_KEY=sk-...
    python3 pipeline/generate.py --limit 1000 --workers 6
    python3 pipeline/generate.py --limit 50            # sample first
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from collections import Counter
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import (  # noqa: E402
    CACHE_FILE,
    CATEGORIES,
    CLEAN_POI_FILE,
    OUTPUT_FILE,
)
from pipeline.prompts import (  # noqa: E402
    SYSTEM_PROMPT,
    build_user_prompt,
    validate_copy,
)
from pipeline.signals import build_signals  # noqa: E402

# A POI with fewer than this many verified signals cannot support grounded
# copy - the model would have to invent. Better to skip it than ship fiction.
MIN_SIGNALS = 2

# Overridable so you can point at Azure OpenAI, a proxy, or a compatible
# provider (DeepSeek, Moonshot, ...) without editing code.
API_URL = os.environ.get(
    "OPENAI_BASE_URL", "https://api.openai.com/v1"
).rstrip("/") + "/chat/completions"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

_cache_lock = threading.Lock()
_print_lock = threading.Lock()


def load_cache() -> dict:
    path = Path(CACHE_FILE)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    path = Path(CACHE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def call_openai(api_key: str, model: str, poi: dict, signals: list[dict],
                temperature: float, profile: dict | None = None) -> dict | None:
    """One chat completion. Returns parsed copy dict or None."""
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(poi, signals, profile)},
        ],
        "temperature": temperature,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    delay = 2.0
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            return json.loads(content)
        except urllib.error.HTTPError as exc:
            # 429 and 5xx are retryable; 4xx generally is not.
            if exc.code in (429, 500, 502, 503, 504):
                time.sleep(delay + random.random())
                delay *= 2
                continue
            with _print_lock:
                print(f"  ! HTTP {exc.code} for {poi['name']}: "
                      f"{exc.read()[:200]!r}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError, KeyError,
                json.JSONDecodeError):
            time.sleep(delay)
            delay *= 2
    return None


def generate_one(api_key: str, model: str, poi: dict,
                 cache: dict, profile: dict | None = None) -> dict | None:
    """
    Generate validated, signal-grounded copy for one POI.

    `profile` (mbti/zodiac/preferences) tailors the copy to the reader. The
    on-disk cache is only used for the generic (profile-less) batch build:
    personalised copy is per-user and must not be cached under the POI id.
    """
    poi_id = poi["id"]
    use_cache = profile is None
    if use_cache:
        with _cache_lock:
            if poi_id in cache:
                return {**poi, **cache[poi_id]}

    signals = build_signals(poi)
    if len(signals) < MIN_SIGNALS:
        # Not enough verified facts to write from. Skipping is the honest
        # choice: the alternative is letting the model invent details.
        with _print_lock:
            print(f"  - skip {poi['name']}: only {len(signals)} signal(s)")
        return None

    # Nudge temperature up on retries to escape a bad phrasing rut.
    for attempt in range(3):
        copy = call_openai(api_key, model, poi, signals, 0.9 + attempt * 0.15,
                           profile)
        if copy is None:
            continue
        ok, reason = validate_copy(copy, signals, profile)
        if ok:
            entry = {k: copy[k].strip()
                     for k in ("hook", "reason", "oracle", "action")}
            # Record which data fields backed this copy, so any line can be
            # audited later ("where did this claim come from?").
            entry["sources"] = sorted({s["source"] for s in signals})
            if use_cache:
                with _cache_lock:
                    cache[poi_id] = entry
            return {**poi, **entry}
        with _print_lock:
            print(f"  ~ retry {poi['name']}: {reason}")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate gacha copy")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: OPENAI_API_KEY is not set.", file=sys.stderr)
        return 1

    clean_path = Path(CLEAN_POI_FILE)
    if not clean_path.exists():
        print(f"ERROR: {clean_path} not found. Run collect.py + clean.py first.",
              file=sys.stderr)
        return 1

    pois = json.loads(clean_path.read_text(encoding="utf-8"))

    # Balance across categories so rotation always has somewhere to go.
    by_cat: dict[str, list[dict]] = {}
    for poi in pois:
        by_cat.setdefault(poi["category"], []).append(poi)
    for bucket in by_cat.values():
        random.shuffle(bucket)

    selected: list[dict] = []
    quota = max(1, args.limit // max(1, len(by_cat)))
    for bucket in by_cat.values():
        selected.extend(bucket[:quota])
    # Top up from the remainder if rounding left us short.
    if len(selected) < args.limit:
        chosen = {p["id"] for p in selected}
        leftovers = [p for p in pois if p["id"] not in chosen]
        random.shuffle(leftovers)
        selected.extend(leftovers[: args.limit - len(selected)])
    selected = selected[: args.limit]

    print(f"Generating copy for {len(selected)} POIs "
          f"({args.workers} workers, model={args.model})")

    cache = load_cache()
    cached_count = sum(1 for p in selected if p["id"] in cache)
    print(f"Cache hits: {cached_count}/{len(selected)}")

    results: list[dict] = []
    failed = 0
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(generate_one, api_key, args.model, poi, cache): poi
            for poi in selected
        }
        for future in as_completed(futures):
            done += 1
            record = future.result()
            if record:
                results.append(record)
            else:
                failed += 1
            if done % 25 == 0:
                with _cache_lock:
                    save_cache(cache)
                with _print_lock:
                    print(f"  progress {done}/{len(selected)}  ok={len(results)}  "
                          f"failed={failed}")

    save_cache(cache)
    report_diversity(results)
    write_output(results)
    print(f"\nDone. {len(results)} records, {failed} failed.")
    return 0


def report_diversity(records: list[dict]) -> None:
    """
    Warn when the model falls into repetitive phrasing.

    Copy is the product's soul; 1000 records that all say the same thing would
    be worse than no data at all. This does not fail the run, but it must be
    visible so you can raise temperature or enrich the prompt.
    """
    if not records:
        return
    print("\n--- copy diversity ---")
    for field in ("hook", "reason", "oracle", "action"):
        values = [r[field] for r in records]
        unique = len(set(values))
        ratio = unique / len(values)
        flag = "OK " if ratio >= 0.75 else "LOW"
        print(f"  [{flag}] {field:7s} {unique}/{len(values)} unique ({ratio:.0%})")
        if ratio < 0.75:
            for text, count in Counter(values).most_common(3):
                if count > 1:
                    print(f"         x{count}: {text[:40]}")


def write_output(records: list[dict]) -> None:
    """Emit the app-facing JSON, trimmed to only what the client needs."""
    payload = []
    for record in records:
        payload.append({
            "id": record["id"],
            # Brand-only name (falls back to raw name for older cache entries).
            "name": record.get("display_name") or record["name"],
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
            # Provenance: which real data fields this copy was written from.
            "sources": record.get("sources", []),
        })

    out = Path(OUTPUT_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"Wrote {len(payload)} records -> {out}")


if __name__ == "__main__":
    raise SystemExit(main())
