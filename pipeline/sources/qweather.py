"""
和风天气 (QWeather) client - the *time* half of our real-data story.

Why this source
---------------
Everything else we collect describes a place. This describes the moment.
Moon phase, sunset time and "feels like" temperature are:
  * genuinely real (astronomical / observed data, not scraped opinion)
  * genuinely mystical-sounding (月相 is the single most on-brand signal we have)
  * useful to *all seven* categories, which is exactly what our weak buckets
    (公园 / 书店 / 小众地方) need - they have no dish tags to lean on.

Two call sites, deliberately different:
  * build time  - 月相 for the next N days, cached, so copy can reference it
  * run time    - current weather, fetched by the app for the draw context

Auth
----
QWeather deprecated plain API keys for new projects in favour of JWT (Ed25519).
We support both:
  QWEATHER_API_KEY   -> legacy `key=` query param
  QWEATHER_KEY_ID + QWEATHER_PROJECT_ID + QWEATHER_PRIVATE_KEY(_FILE) -> JWT
JWT signing uses PyNaCl or `cryptography` if present; if neither is installed
we say so clearly rather than failing with an opaque crypto error.

Every function degrades to None instead of raising. A missing weather signal
must never break the pipeline - it just means one fewer signal for that POI.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# Host is per-account on QWeather's new plans (e.g. abcxyz.qweatherapi.com).
DEFAULT_HOST = "https://devapi.qweather.com"

# 朝阳区 city center, used for district-level weather and astronomy.
CHAOYANG_LOCATION = "116.4864,39.9219"

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _host() -> str:
    return os.environ.get("QWEATHER_HOST", DEFAULT_HOST).rstrip("/")


# --- JWT (Ed25519) ---------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_private_key() -> str | None:
    key = os.environ.get("QWEATHER_PRIVATE_KEY", "").strip()
    if key:
        # Allow the PEM to be passed as a single line with escaped newlines.
        return key.replace("\\n", "\n")
    path = os.environ.get("QWEATHER_PRIVATE_KEY_FILE", "").strip()
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    return None


def _sign_ed25519(message: bytes, pem: str) -> bytes | None:
    """Sign with whichever crypto library is available."""
    try:
        from cryptography.hazmat.primitives import serialization

        private_key = serialization.load_pem_private_key(
            pem.encode("utf-8"), password=None
        )
        return private_key.sign(message)
    except ImportError:
        pass
    except Exception:
        return None

    try:
        from nacl.signing import SigningKey  # type: ignore

        body = "".join(
            line for line in pem.splitlines() if not line.startswith("-----")
        )
        raw = base64.b64decode(body)
        # PKCS#8 Ed25519 keys end with the 32-byte seed.
        signing_key = SigningKey(raw[-32:])
        return signing_key.sign(message).signature
    except ImportError:
        return None
    except Exception:
        return None


def _jwt_token() -> str | None:
    """Build (and cache) a QWeather JWT. Returns None if not configured."""
    key_id = os.environ.get("QWEATHER_KEY_ID", "").strip()
    project_id = os.environ.get("QWEATHER_PROJECT_ID", "").strip()
    if not key_id or not project_id:
        return None

    cached = _TOKEN_CACHE.get("jwt")
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    pem = _load_private_key()
    if not pem:
        return None

    now = int(time.time())
    expires = now + 900  # 15 minutes; QWeather allows up to 24h.
    header = {"alg": "EdDSA", "kid": key_id}
    payload = {"sub": project_id, "iat": now - 30, "exp": expires}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    ).encode("ascii")

    signature = _sign_ed25519(signing_input, pem)
    if signature is None:
        print("  ! QWeather JWT signing unavailable: "
              "pip install cryptography (or pynacl)")
        return None

    token = signing_input.decode("ascii") + "." + _b64url(signature)
    _TOKEN_CACHE["jwt"] = (token, expires)
    return token


def is_configured() -> bool:
    return bool(
        os.environ.get("QWEATHER_API_KEY", "").strip()
        or (os.environ.get("QWEATHER_KEY_ID", "").strip()
            and os.environ.get("QWEATHER_PROJECT_ID", "").strip())
    )


def _get(path: str, params: dict) -> dict | None:
    """Signed GET against the QWeather API. None on any failure."""
    headers = {"Accept-Encoding": "identity"}
    token = _jwt_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        api_key = os.environ.get("QWEATHER_API_KEY", "").strip()
        if not api_key:
            return None
        params = {**params, "key": api_key}

    url = f"{_host()}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=headers)
    delay = 1.0
    for _ in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(delay)
            delay *= 2
            continue
        # QWeather uses "200" for success; anything else is terminal for us.
        if payload.get("code") != "200":
            print(f"  ! QWeather {path} returned code {payload.get('code')}")
            return None
        return payload
    return None


# --- Public API ------------------------------------------------------------

def moon_phase(date: str, location: str = CHAOYANG_LOCATION) -> dict | None:
    """
    Moon phase for a given date ("YYYYMMDD").

    Returns {"phase": "满月", "illumination": 99, "moonrise": "18:12"} or None.
    QWeather already returns the phase name in Chinese, which is exactly the
    vocabulary our MOON_LORE table is keyed on - no mapping guesswork.
    """
    payload = _get("/v7/astronomy/moon", {"location": location, "date": date})
    if not payload:
        return None
    phases = payload.get("moonPhase") or []
    if not phases:
        return None
    # The API returns hourly entries; the evening one is what "今晚" means.
    evening = next(
        (p for p in phases if p.get("fxTime", "")[11:13] in ("20", "21", "19")),
        phases[len(phases) // 2],
    )
    return {
        "phase": evening.get("name"),
        "illumination": evening.get("illumination"),
        "moonrise": (payload.get("moonrise") or "")[-5:] or None,
        "moonset": (payload.get("moonset") or "")[-5:] or None,
    }


def sun_times(date: str, location: str = CHAOYANG_LOCATION) -> dict | None:
    """Sunrise/sunset as "HH:MM". Drives the 「日落前一小时」 signal."""
    payload = _get("/v7/astronomy/sun", {"location": location, "date": date})
    if not payload:
        return None
    sunrise = (payload.get("sunrise") or "")[-5:]
    sunset = (payload.get("sunset") or "")[-5:]
    if not sunset:
        return None
    return {"sunrise": sunrise or None, "sunset": sunset}


def current_weather(location: str = CHAOYANG_LOCATION) -> dict | None:
    """
    Live conditions. Used at run time, not build time.

    `feelsLike` matters more than `temp` for a "should I go outside now?"
    product, and `vis` (visibility) is what makes 「今天能看多远」 honest.
    """
    payload = _get("/v7/weather/now", {"location": location})
    if not payload:
        return None
    now = payload.get("now") or {}
    return {
        "text": now.get("text"),
        "temp": now.get("temp"),
        "feels_like": now.get("feelsLike"),
        "wind_dir": now.get("windDir"),
        "wind_scale": now.get("windScale"),
        "humidity": now.get("humidity"),
        "visibility": now.get("vis"),
        "precip": now.get("precip"),
    }


def daily_forecast(location: str = CHAOYANG_LOCATION, days: int = 3) -> list[dict]:
    """Next few days, so build-time copy can say "明天" honestly."""
    endpoint = "/v7/weather/3d" if days <= 3 else "/v7/weather/7d"
    payload = _get(endpoint, {"location": location})
    if not payload:
        return []
    out = []
    for day in (payload.get("daily") or [])[:days]:
        out.append({
            "date": day.get("fxDate"),
            "text_day": day.get("textDay"),
            "temp_max": day.get("tempMax"),
            "temp_min": day.get("tempMin"),
            "sunset": day.get("sunset"),
            "moon_phase": day.get("moonPhase"),
        })
    return out
