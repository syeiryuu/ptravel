/**
 * Backend API client.
 *
 * The app draws instantly in the browser (see gacha.ts), so the network is
 * never on the critical path of a draw. This client only:
 *   - fetches the suggestion pool (with a static-JSON fallback), and
 *   - fire-and-forgets behaviour (profile saves, draw records).
 *
 * Every call degrades silently: if the FastAPI service isn't running, the app
 * still works off the bundled static pool exactly as it did before the backend
 * existed. That keeps the offline/preview experience intact.
 */

import type { Suggestion } from './gacha'

/**
 * Where the behaviour/pool API lives.
 *
 * - In dev, the Vite server proxies /api to the local FastAPI, so we always try
 *   it (empty base -> same-origin /api/...).
 * - In a production *static* deploy (e.g. GitHub Pages) there is no backend, so
 *   we skip the API entirely unless VITE_API_BASE points at a real one. This
 *   keeps the static site from firing doomed /api/* requests on every load; the
 *   app just uses the bundled JSON and the personality weighting in gacha.ts.
 *
 * Set VITE_API_BASE="https://your-api.example.com" at build time to re-enable
 * the backend (pool weighting + draw/profile logging) for a hosted deployment.
 */
const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

/** True when we should talk to a backend at all. */
function hasBackend(): boolean {
  return import.meta.env.DEV || API_BASE !== ''
}

/** Build a full API URL, honouring an optional external base. */
function apiUrl(path: string): string {
  return API_BASE ? `${API_BASE.replace(/\/$/, '')}${path}` : path
}

/** Region comes from ?region=, defaulting to Beijing 朝阳区. */
export function currentRegion(): string {
  const region = new URLSearchParams(window.location.search).get('region')
  return region && region.trim() ? region.trim() : 'chaoyang'
}

/** Static-file path for a region, the last-resort pool source. */
function staticPoolPath(region: string): string {
  const suffix = region === 'chaoyang' ? '' : `_${region}`
  return `${import.meta.env.BASE_URL}data/gacha${suffix}.json`
}

/** A stable anonymous id, so draw/profile rows can be grouped per browser. */
const USER_ID_KEY = 'ptravel.userId'
export function userId(): string {
  try {
    let id = localStorage.getItem(USER_ID_KEY)
    if (!id) {
      id =
        typeof crypto !== 'undefined' && 'randomUUID' in crypto
          ? crypto.randomUUID()
          : `u_${Date.now()}_${Math.random().toString(36).slice(2)}`
      localStorage.setItem(USER_ID_KEY, id)
    }
    return id
  } catch {
    // Private mode: a per-session id is fine, it just won't persist.
    return `anon_${Math.random().toString(36).slice(2)}`
  }
}

interface PoolResponse {
  region: string
  source: string
  count: number
  items: Suggestion[]
}

/**
 * Load the suggestion pool for the given profile.
 *
 * Tries the API first (which applies the offline-built, profile-weighted pool);
 * on any failure or empty result, falls back to the bundled static JSON so the
 * app is always drawable.
 */
export async function fetchPool(params: {
  region: string
  mbti?: string
  preferences?: string[]
}): Promise<Suggestion[]> {
  const q = new URLSearchParams({ region: params.region })
  if (params.mbti) q.set('mbti', params.mbti)
  if (params.preferences?.length) q.set('preferences', params.preferences.join(','))

  // Only hit the API when one actually exists (dev proxy, or a configured
  // VITE_API_BASE). A static deploy skips straight to the bundled JSON.
  if (hasBackend()) {
    try {
      const res = await fetch(apiUrl(`/api/pool?${q.toString()}`))
      if (res.ok) {
        const data: PoolResponse = await res.json()
        if (Array.isArray(data.items) && data.items.length > 0) return data.items
      }
    } catch {
      // fall through to static
    }
  }

  // Fallback: bundled static pool (identical shape, already camelCase).
  try {
    const res = await fetch(staticPoolPath(params.region))
    if (res.ok) {
      const items: Suggestion[] = await res.json()
      if (Array.isArray(items)) return items
    }
  } catch {
    // offline & no bundle: caller handles an empty pool gracefully
  }
  return []
}

/** Persist the user's 我的 profile. Fire-and-forget; never blocks the UI. */
export function saveProfile(profile: {
  mbti: string
  zodiac: string
  preferences: string[]
}): void {
  const body = JSON.stringify({
    userId: userId(),
    mbti: profile.mbti,
    zodiac: profile.zodiac,
    preferences: profile.preferences,
    region: currentRegion(),
  })
  if (!hasBackend()) return // static deploy: nothing to sync to
  void fetch(apiUrl('/api/profile'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {
    // Offline / no backend: the profile still lives in localStorage.
  })
}

/** Record one draw for later T+1 analysis. Fire-and-forget. */
export function recordDraw(draw: {
  id: string
  luck: number
  isSuper: boolean
}): void {
  const body = JSON.stringify({
    userId: userId(),
    id: draw.id,
    luck: draw.luck,
    isSuper: draw.isSuper,
    region: currentRegion(),
  })
  if (!hasBackend()) return // static deploy: no logging endpoint
  void fetch(apiUrl('/api/draw'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {
    // A missing draw record is not worth bothering the user about.
  })
}
