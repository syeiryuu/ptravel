/**
 * Suggestion selection engine.
 *
 * Encodes two product rules that the data alone cannot express:
 *
 * 1. Category rotation. Re-rolling means the user rejected the *kind* of thing
 *    we offered, not just that one venue. So the next draw must come from a
 *    different category, and recently shown categories stay suppressed.
 *
 * 2. Proximity + openness. A suggestion must be startable now and nearby,
 *    otherwise it breaks the core promise.
 */

export type Rarity = 'common' | 'uncommon' | 'rare'

export interface Suggestion {
  id: string
  name: string
  category: string
  categoryLabel: string
  area: string
  address: string
  lng: number
  lat: number
  openHour: number
  closeHour: number
  durationMinutes: number
  direction: string
  rarity: Rarity
  lucky: number
  hook: string
  reason: string
  oracle: string
  action: string
}

/** The bits of the user's 我的 profile that affect the weighted draw. */
export interface DrawProfile {
  /** 4-letter MBTI, e.g. "INFP". Empty when not set. */
  mbti?: string
  /** 星座, e.g. "双鱼座". Reserved for a light directional nudge. */
  zodiac?: string
  /** 今日偏好 ids, e.g. ["forage", "idle"]. */
  preferences?: string[]
}

export interface DrawContext {
  /** User position; when absent, distance filtering is skipped. */
  origin?: { lng: number; lat: number }
  /** Hour of day 0-23. Defaults to now. */
  hour?: number
  /** Categories shown recently, most recent last. */
  recentCategories?: string[]
  /** Ids already shown in this session. */
  seenIds?: string[]
  /** Max walking/short-ride distance in km. */
  maxDistanceKm?: number
  /** User profile — drives the personality/preference weighting. */
  profile?: DrawProfile
  /**
   * Busyness of each 商圈 (business area), 0..1 as a share of the densest.
   * Mirrors recommend._area_heat on the backend; used by the E/I axis. When
   * absent it is computed once from the pool.
   */
  areaHeat?: Record<string, number>
}

const EARTH_RADIUS_KM = 6371

/** Great-circle distance in km. */
export function distanceKm(
  a: { lng: number; lat: number },
  b: { lng: number; lat: number },
): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180
  const dLat = toRad(b.lat - a.lat)
  const dLng = toRad(b.lng - a.lng)
  const lat1 = toRad(a.lat)
  const lat2 = toRad(b.lat)
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.sin(dLng / 2) ** 2 * Math.cos(lat1) * Math.cos(lat2)
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(h))
}

/**
 * Is the venue open at `hour`, and is there enough time left to enjoy it?
 * closeHour may exceed 24 to express "closes after midnight".
 */
export function isOpenAt(item: Suggestion, hour: number): boolean {
  const open = item.openHour ?? 0
  const close = item.closeHour ?? 24
  const normalisedHour = hour < open && close > 24 ? hour + 24 : hour
  if (normalisedHour < open) return false
  // Require at least half the dwell time before closing, so we never send
  // someone to a place that shuts in ten minutes.
  const hoursNeeded = (item.durationMinutes ?? 60) / 60 / 2
  return normalisedHour + hoursNeeded <= close
}

// --- Personality / preference weighting -------------------------------------
// Kept deliberately in sync with pipeline/db/recommend.py so an offline-built
// pool and a live client draw agree. The frontend has fewer fields than the
// backend (no rating/cost), so the axes that need those simply don't fire here
// — the rule degrades to whatever data is present, exactly like the backend.

// 今日偏好 -> favoured categories (mirrors PREFERENCE_CATEGORIES).
// Each pill lists categories from both regions (Beijing cafe/food/park...;
// Dolomites hut/peak/lake...). Keys are disjoint bar "food", so a category
// simply never fires on the region that lacks it — the rule degrades silently.
const PREFERENCE_CATEGORIES: Record<string, readonly string[]> = {
  forage: ['food', 'hut'], // 觅食：吃
  sweat: ['park', 'trail', 'peak'], // 流汗：走/爬
  stroll: ['park', 'shop', 'village', 'lake'], // 溜达：逐
  idle: ['cafe', 'park', 'lake', 'peak', 'cable'], // 放空：发呆
  fate: [], // 随缘 -> no nudge
}

// MBTI axes -> favoured categories (mirrors MBTI_*_CATEGORIES).
const MBTI_EI_CATEGORIES: Record<string, readonly string[]> = {
  E: ['night', 'food', 'shop', 'village', 'cable'], // 外向：人聚的地方
  I: ['cafe', 'culture', 'park', 'peak', 'lake', 'trail'], // 内向：人少的地方
}
const MBTI_SN_CATEGORIES: Record<string, readonly string[]> = {
  S: ['food', 'shop', 'park', 'village', 'hut'], // 实感：具体可体验
  N: ['weird', 'culture', 'night', 'peak', 'trail', 'lake'], // 直觉：风景与想象
}
const MBTI_TF_CATEGORIES: Record<string, readonly string[]> = {
  T: ['culture', 'weird', 'village', 'trail'], // 思考：信息量/路书感
  F: ['cafe', 'food', 'park', 'hut', 'lake'], // 情感：暖、慢、舒服
}

const PREFERENCE_BOOST = 1.5
const MBTI_AXIS_BOOST = 1.18
const MBTI_JP_HOURS_BOOST = 1.15
const MBTI_MAX = 1.9

/** Combined MBTI nudge from the axes the frontend has data for. */
function mbtiMultiplier(
  item: Suggestion,
  mbti: string,
  areaHeat?: Record<string, number>,
): number {
  if (!mbti || mbti.length < 4) return 1
  const m = mbti.toUpperCase()
  const ei = m.includes('E') ? 'E' : m.includes('I') ? 'I' : ''
  const sn = m.includes('S') ? 'S' : m.includes('N') ? 'N' : ''
  const tf = m.includes('T') ? 'T' : m.includes('F') ? 'F' : ''
  const jp = m.includes('J') ? 'J' : m.includes('P') ? 'P' : ''
  const category = item.category
  let mult = 1

  // E/I: category + business-area busyness.
  if (ei && MBTI_EI_CATEGORIES[ei]?.includes(category)) mult *= MBTI_AXIS_BOOST
  const heat = areaHeat?.[item.area]
  if (ei && heat !== undefined) {
    if (ei === 'E' && heat >= 0.66) mult *= 1.12
    else if (ei === 'I' && heat <= 0.33) mult *= 1.12
    else if (ei === 'E' && heat <= 0.33) mult *= 0.9
    else if (ei === 'I' && heat >= 0.66) mult *= 0.9
  }

  // S/N and T/F: category only (no rating/cost on the client).
  if (sn && MBTI_SN_CATEGORIES[sn]?.includes(category)) mult *= MBTI_AXIS_BOOST
  if (tf && MBTI_TF_CATEGORIES[tf]?.includes(category)) mult *= MBTI_AXIS_BOOST

  // J/P: opening hours + dwell time.
  if (jp && item.closeHour != null) {
    if (jp === 'P' && item.closeHour >= 22) mult *= MBTI_JP_HOURS_BOOST
    else if (jp === 'J' && item.closeHour <= 21) mult *= MBTI_JP_HOURS_BOOST
  }
  if (jp === 'J' && item.durationMinutes != null && item.durationMinutes <= 90) {
    mult *= 1.06
  }

  return Math.min(mult, MBTI_MAX)
}

function weightFor(
  item: Suggestion,
  profile?: DrawProfile,
  areaHeat?: Record<string, number>,
): number {
  // The pool is already skewed by the data pipeline's rarity assignment, so we
  // only apply a mild extra damping here. ~3-5% keeps rare draws a genuine
  // possibility without letting them dominate.
  let weight: number
  switch (item.rarity) {
    case 'rare':
      weight = 0.45
      break
    case 'uncommon':
      weight = 0.75
      break
    default:
      weight = 1
  }

  if (profile) {
    // Preference nudge — the strongest signal, an explicit choice.
    const favoured = new Set<string>()
    for (const pref of profile.preferences ?? []) {
      for (const cat of PREFERENCE_CATEGORIES[pref] ?? []) favoured.add(cat)
    }
    if (favoured.has(item.category)) weight *= PREFERENCE_BOOST

    // MBTI nudge — all axes the client has data for.
    if (profile.mbti) weight *= mbtiMultiplier(item, profile.mbti, areaHeat)
  }

  return weight
}

/** Busyness of each 商圈 as a 0..1 share of the densest, computed from a pool. */
export function areaHeatFromPool(pool: Suggestion[]): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const item of pool) {
    if (item.area) counts[item.area] = (counts[item.area] ?? 0) + 1
  }
  const max = Math.max(1, ...Object.values(counts))
  const heat: Record<string, number> = {}
  for (const [area, n] of Object.entries(counts)) heat[area] = n / max
  return heat
}

function weightedPick(
  items: Suggestion[],
  profile?: DrawProfile,
  areaHeat?: Record<string, number>,
): Suggestion {
  const total = items.reduce((sum, item) => sum + weightFor(item, profile, areaHeat), 0)
  let roll = Math.random() * total
  for (const item of items) {
    roll -= weightFor(item, profile, areaHeat)
    if (roll <= 0) return item
  }
  return items[items.length - 1]
}

/**
 * Draw one suggestion.
 *
 * Filters are relaxed progressively rather than all at once, so we degrade to
 * a slightly worse match instead of returning nothing.
 */
export function draw(
  pool: Suggestion[],
  context: DrawContext = {},
): Suggestion | null {
  if (pool.length === 0) return null

  const hour = context.hour ?? new Date().getHours()
  const seen = new Set(context.seenIds ?? [])
  const recent = context.recentCategories ?? []
  const maxDistance = context.maxDistanceKm ?? 3
  const profile = context.profile
  // Area busyness for the E/I axis; compute once from the pool if not supplied.
  const areaHeat = context.areaHeat ?? areaHeatFromPool(pool)
  // Suppress the last two categories so rotation feels real without
  // exhausting a small pool.
  const blocked = new Set(recent.slice(-2))

  const withDistance = pool.map((item) => ({
    item,
    distance: context.origin ? distanceKm(context.origin, item) : 0,
  }))

  const tiers: Array<(entry: { item: Suggestion; distance: number }) => boolean> = [
    // Tier 1: everything we want.
    ({ item, distance }) =>
      !seen.has(item.id) &&
      !blocked.has(item.category) &&
      isOpenAt(item, hour) &&
      distance <= maxDistance,
    // Tier 2: allow a longer trip.
    ({ item, distance }) =>
      !seen.has(item.id) &&
      !blocked.has(item.category) &&
      isOpenAt(item, hour) &&
      distance <= maxDistance * 2.5,
    // Tier 3: allow repeating a recent category.
    ({ item }) => !seen.has(item.id) && isOpenAt(item, hour),
    // Tier 4: ignore opening hours, but never repeat within a session.
    ({ item }) => !seen.has(item.id),
    // Tier 5: anything.
    () => true,
  ]

  for (const predicate of tiers) {
    const candidates = withDistance.filter(predicate)
    if (candidates.length > 0) {
      return weightedPick(candidates.map((entry) => entry.item), profile, areaHeat)
    }
  }
  return null
}

// --- Navigation ------------------------------------------------------------
// Picking the right map is not one choice but two, and they must agree:
//
//   1. Coordinate system. AMap/Baidu expect GCJ-02 ("Mars") coordinates, which
//      is what our Beijing data already is. OpenStreetMap (the Dolomites) is
//      plain WGS-84. Feeding WGS-84 to AMap lands you hundreds of metres off —
//      or, abroad, on a blank map — so a domestic map is only correct for the
//      domestic, GCJ-02 dataset.
//   2. Coverage. AMap has no navigable data outside China, and Apple/Google
//      have no reliable footpath routing inside it. So the region decides the
//      family of maps, and the device decides which member of that family.
//
// `region` comes from ?region= (see api.currentRegion). Anything that isn't a
// Chinese, GCJ-02 region is treated as overseas / WGS-84.
const DOMESTIC_REGIONS = new Set(['chaoyang'])

function isAppleDevice(): boolean {
  if (typeof navigator === 'undefined') return false
  // iPhone / iPad / iPod / Mac. iPadOS 13+ reports as "Macintosh", which is
  // fine: Apple Maps is the right default there too.
  return /iphone|ipad|ipod|macintosh/i.test(navigator.userAgent)
}

/**
 * A map deep link for the "导航过去" button, chosen for the region and device.
 *
 * - Domestic (Beijing, GCJ-02): AMap, which has the walking routes and matches
 *   the coordinate system of that data.
 * - Overseas (Dolomites, WGS-84): Apple Maps on Apple devices, Google Maps
 *   elsewhere — both take raw WGS-84 and both actually cover the Alps.
 *
 * The label side is left to the map: we always pass the coordinates (never
 * only a name), so the pin is exact even when the venue name is in Italian.
 */
export function navigationUrl(item: Suggestion, region = 'chaoyang'): string {
  const name = encodeURIComponent(item.name)
  const { lat, lng } = item

  if (DOMESTIC_REGIONS.has(region)) {
    // AMap URI API. coordinate=gaode says "these are already GCJ-02"; src must
    // be URL-encoded or the whole link can be rejected.
    const src = encodeURIComponent('下一站扭蛋')
    return (
      `https://uri.amap.com/navigation?to=${lng},${lat},${name}` +
      `&mode=walk&coordinate=gaode&callnative=1&src=${src}`
    )
  }

  // Overseas, WGS-84.
  if (isAppleDevice()) {
    // Apple Maps: daddr as "lat,lng", dirflg=w for walking.
    return `https://maps.apple.com/?daddr=${lat},${lng}&dirflg=w&q=${name}`
  }
  // Google Maps universal URL, walking mode.
  return (
    `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}` +
    `&travelmode=walking`
  )
}
