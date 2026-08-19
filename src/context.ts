/**
 * Run-time context: what is true *right now*, as opposed to what is true about
 * a place in general.
 *
 * Build-time copy (gacha.json) can only know place facts. But whether you
 * should go outside depends on the moment: it is raining, the sun sets in
 * forty minutes, tonight is a full moon. This module supplies those, and the
 * result is shown as a line of "conditions" above the suggestion - the same
 * 虚实相应 rule as everywhere else: the fact is measured, the phrasing is not.
 *
 * Everything here degrades silently. No location permission, no weather key,
 * no network - the app still works, it just says less.
 */

export interface RuntimeContext {
  /** Hour of day, 0-23. Always available. */
  hour: number
  /** The origin used for distance/direction. Always present: a real fix when
   *  granted, otherwise the default origin below. */
  origin?: { lng: number; lat: number }
  /** Where `origin` came from, so the UI can hint quietly when it is a guess.
   *  'amap'/'gps' = a real fix; 'default' = fell back to 恒电大厦. */
  locationSource?: 'amap' | 'gps' | 'default'
  /** Present only when the weather proxy is configured and reachable. */
  weather?: {
    text: string
    temp: string
    feelsLike: string
    visibility?: string
  }
  moonPhase?: string
  sunset?: string
}

/**
 * Fallback origin when we cannot get a real fix (declined, WeChat blocked the
 * API, no map key, timeout...). Chosen to be a concrete, real building rather
 * than a district centroid so the distances/directions read sensibly.
 *
 * 朝阳区恒电大厦 · GCJ-02 (高德坐标系). Source: 高德地图长按取点。
 * 纬度 40.008555, 经度 116.487901.
 */
export const DEFAULT_ORIGIN = { lng: 116.487901, lat: 40.008555 }

/** Amap Web(JS API) key, read from env. Absent -> skip Amap, degrade quietly. */
const AMAP_KEY = import.meta.env.VITE_AMAP_KEY as string | undefined

/**
 * Load the AMap JS SDK once and resolve when `window.AMap` is ready.
 * Rejects (resolves false) when there is no key or the script fails.
 */
let amapLoader: Promise<boolean> | null = null
function loadAmap(): Promise<boolean> {
  if (amapLoader) return amapLoader
  amapLoader = new Promise<boolean>((resolve) => {
    if (!AMAP_KEY || typeof document === 'undefined') {
      resolve(false)
      return
    }
    // Already present.
    if ((window as unknown as { AMap?: unknown }).AMap) {
      resolve(true)
      return
    }
    const script = document.createElement('script')
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${AMAP_KEY}&plugin=AMap.Geolocation`
    script.async = true
    const timer = setTimeout(() => resolve(false), 6000)
    script.onload = () => {
      clearTimeout(timer)
      resolve(!!(window as unknown as { AMap?: unknown }).AMap)
    }
    script.onerror = () => {
      clearTimeout(timer)
      resolve(false)
    }
    document.head.appendChild(script)
  })
  return amapLoader
}

/**
 * Locate via AMap. Returns GCJ-02 coordinates directly (no conversion needed),
 * which is what the domestic map / navigation links expect. AMap also falls
 * back to IP-based location on its own when precise positioning fails.
 */
function locateViaAmap(
  timeoutMs = 6000,
): Promise<{ lng: number; lat: number } | undefined> {
  return new Promise((resolve) => {
    loadAmap().then((ok) => {
      const AMap = (window as unknown as { AMap?: any }).AMap
      if (!ok || !AMap) {
        resolve(undefined)
        return
      }
      try {
        const geolocation = new AMap.Geolocation({
          enableHighAccuracy: false,
          timeout: timeoutMs,
          // Let AMap fall back to IP location when GPS/precise fails.
          noIpLocate: 0,
          GeoLocationFirst: true,
        })
        geolocation.getCurrentPosition((status: string, result: any) => {
          if (status === 'complete' && result?.position) {
            resolve({
              lng: result.position.lng,
              lat: result.position.lat,
            })
          } else {
            resolve(undefined)
          }
        })
      } catch {
        resolve(undefined)
      }
    })
  })
}

/**
 * Ask the browser for a position.
 *
 * Deliberately short-fused: this runs while the user is watching a gacha
 * animation, and a suggestion that arrives late is worse than one that ignores
 * distance. A denied or slow permission simply resolves to undefined.
 */
function requestNativeOrigin(
  timeoutMs = 6000,
): Promise<{ lng: number; lat: number } | undefined> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    return Promise.resolve(undefined)
  }
  return new Promise((resolve) => {
    let settled = false
    const finish = (value?: { lng: number; lat: number }) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    // Belt and braces: some browsers ignore the timeout option entirely.
    const timer = setTimeout(() => finish(undefined), timeoutMs)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        clearTimeout(timer)
        finish({
          lng: position.coords.longitude,
          lat: position.coords.latitude,
        })
      },
      () => {
        clearTimeout(timer)
        finish(undefined)
      },
      { enableHighAccuracy: false, timeout: timeoutMs, maximumAge: 300_000 },
    )
  })
}

/**
 * Resolve an origin plus where it came from. Strategy, best first:
 *   1. AMap (when a key is configured) - works inside WeChat, returns GCJ-02.
 *   2. Native browser geolocation - usually blocked in WeChat, but free.
 *   3. Default origin (恒电大厦) - always succeeds, flagged so the UI can hint.
 * Never rejects.
 */
export async function requestOrigin(): Promise<{
  origin: { lng: number; lat: number }
  source: 'amap' | 'gps' | 'default'
}> {
  const viaAmap = await locateViaAmap()
  if (viaAmap) return { origin: viaAmap, source: 'amap' }

  const viaNative = await requestNativeOrigin()
  if (viaNative) return { origin: viaNative, source: 'gps' }

  return { origin: DEFAULT_ORIGIN, source: 'default' }
}

/**
 * Fetch live conditions.
 *
 * Routed through our own endpoint rather than calling QWeather directly: the
 * API credential must not ship in client-side JavaScript, where anyone can
 * read it out of the bundle. Absent that endpoint, we return undefined and the
 * app carries on without weather.
 */
export async function fetchConditions(): Promise<Partial<RuntimeContext>> {
  // Defaults to the same-origin endpoint served by the Vite plugin in dev and
  // by a serverless function in production. Override only to point elsewhere.
  const endpoint =
    import.meta.env.VITE_WEATHER_ENDPOINT ?? '/api/conditions'
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 4000)
    const response = await fetch(endpoint, { signal: controller.signal })
    clearTimeout(timer)
    if (!response.ok) return {}
    const data = await response.json()
    return {
      weather: data.weather,
      moonPhase: data.moonPhase,
      sunset: data.sunset,
    }
  } catch {
    // Weather is a bonus signal, never a dependency.
    return {}
  }
}

/** Collect everything available about "now". Never rejects. */
export async function loadRuntimeContext(): Promise<RuntimeContext> {
  const hour = new Date().getHours()
  const [located, conditions] = await Promise.all([
    requestOrigin(),
    fetchConditions(),
  ])
  return {
    hour,
    origin: located.origin,
    locationSource: located.source,
    ...conditions,
  }
}

// --- Presentation ----------------------------------------------------------

/** Minutes until sunset, or null when we do not know or it has passed. */
export function minutesToSunset(sunset: string | undefined, now = new Date()):
  | number
  | null {
  if (!sunset || !sunset.includes(':')) return null
  const [h, m] = sunset.split(':').map(Number)
  if (Number.isNaN(h) || Number.isNaN(m)) return null
  const target = new Date(now)
  target.setHours(h, m, 0, 0)
  const diff = Math.round((target.getTime() - now.getTime()) / 60000)
  return diff > 0 ? diff : null
}

/**
 * One short line describing the present moment, shown above the suggestion.
 *
 * Only states things we actually measured. When we know nothing, it returns
 * the time of day, which is always true.
 */
export function describeMoment(context: RuntimeContext, now = new Date()): string {
  const parts: string[] = []

  const untilSunset = minutesToSunset(context.sunset, now)
  if (untilSunset !== null && untilSunset <= 120) {
    parts.push(`距日落 ${untilSunset} 分钟`)
  }

  if (context.weather) {
    const { text, feelsLike, temp } = context.weather
    const degrees = feelsLike || temp
    parts.push(degrees ? `${text} 体感${degrees}°` : text)
  }

  if (context.moonPhase && (context.hour >= 18 || context.hour < 5)) {
    parts.push(`今夜${context.moonPhase}`)
  }

  if (parts.length === 0) {
    const hour = context.hour
    if (hour < 6) return '夜里还醒着的时辰'
    if (hour < 11) return '上午'
    if (hour < 14) return '正午前后'
    if (hour < 17) return '下午'
    if (hour < 19) return '天要黑了'
    return '入夜'
  }
  return parts.join(' · ')
}
