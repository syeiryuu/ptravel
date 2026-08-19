import { useEffect, useRef, useState, type ImgHTMLAttributes } from 'react'
import machineSmall from './assets/gacha/machine-small.webp'
import machineLarge from './assets/gacha/machine-large.webp'
import machineBackground from './assets/gacha/machine-background.webp'
import wordmark from './assets/gacha/wordmark.webp'
import profileTitle from './assets/gacha/profile-title.webp'
import iconMbti from './assets/gacha/icon-mbti.webp'
import iconZodiac from './assets/gacha/icon-zodiac.webp'
import iconHeart from './assets/gacha/icon-heart.webp'
import badgeNormal from './assets/gacha/badge-normal.webp'
import badgeSuper from './assets/gacha/badge-super.webp'
import {
  bearingDirection,
  distanceKm,
  draw as drawSuggestion,
  navigationUrl,
  type Suggestion,
} from './gacha'
import {
  loadRuntimeContext,
  type RuntimeContext,
} from './context'
import { currentRegion, fetchPool, recordDraw, saveProfile } from './api'
import './App.css'

type Tab = 'main' | 'profile'
type Page = 'welcome' | 'machine' | 'result'
type Phase = 'idle' | 'shaking' | 'dropping' | 'opening'

const SHAKE_MS = 620
const DROP_MS = 620
const OPEN_MS = 620

// ---- Luck rolling ----
// Every draw rolls a fresh luck value 60–999. Crossing 500 is "super lucky",
// a rare ~4% ceremony. Kept independent of the venue data so the number feels
// like a live blessing rather than a fixed attribute of the place.
const SUPER_LUCKY_THRESHOLD = 500
const SUPER_LUCKY_RATE = 0.04 // 4%, within the requested 3–5% band

type Luck = { value: number; isSuper: boolean }

const randInt = (min: number, max: number) =>
  Math.floor(Math.random() * (max - min + 1)) + min

function rollLuck(): Luck {
  if (Math.random() < SUPER_LUCKY_RATE) {
    // Super lucky: 501–999
    return { value: randInt(SUPER_LUCKY_THRESHOLD + 1, 999), isSuper: true }
  }
  // Ordinary luck: 60–500
  return { value: randInt(60, SUPER_LUCKY_THRESHOLD), isSuper: false }
}

// ---- Profile (我的) options ----
const MBTI_TYPES = [
  'INTJ', 'INTP', 'ENTJ', 'ENTP',
  'INFJ', 'INFP', 'ENFJ', 'ENFP',
  'ISTJ', 'ISFJ', 'ESTJ', 'ESFJ',
  'ISTP', 'ISFP', 'ESTP', 'ESFP',
]

const ZODIAC_SIGNS = [
  '白羊座', '金牛座', '双子座', '巨蟹座',
  '狮子座', '处女座', '天秤座', '天蝎座',
  '射手座', '摩羯座', '水瓶座', '双鱼座',
]

// Each preference has its own accent colour so the selected pill lights up
// in a distinct hue, matching the reference design.
const PREFERENCES = [
  { id: 'forage', label: '觅食', color: 'blue' },
  { id: 'sweat', label: '流汗', color: 'green' },
  { id: 'stroll', label: '溜达', color: 'peach' },
  { id: 'idle', label: '放空', color: 'lilac' },
  { id: 'fate', label: '随缘', color: 'pink' },
] as const

type ProfileData = {
  mbti: string
  zodiac: string
  preferences: string[]
}

const PROFILE_KEY = 'ptravel.profile'

function loadProfile(): ProfileData {
  try {
    const raw = localStorage.getItem(PROFILE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      return {
        mbti: typeof parsed.mbti === 'string' ? parsed.mbti : '',
        zodiac: typeof parsed.zodiac === 'string' ? parsed.zodiac : '',
        preferences: Array.isArray(parsed.preferences) ? parsed.preferences : [],
      }
    }
  } catch {
    // ignore malformed storage
  }
  return { mbti: '', zodiac: '', preferences: [] }
}

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

function App() {
  const [tab, setTab] = useState<Tab>('main')
  const [page, setPage] = useState<Page>('welcome')
  const [phase, setPhase] = useState<Phase>('idle')
  const [leaving, setLeaving] = useState(false)
  // Bumped on every draw so Result remounts and replays its entrance.
  const [drawId, setDrawId] = useState(0)
  const [pool, setPool] = useState<Suggestion[]>([])
  const [current, setCurrent] = useState<Suggestion | null>(null)
  // Luck is rolled fresh on every reveal (see rollLuck), independent of data.
  const [luck, setLuck] = useState<Luck>({ value: 0, isSuper: false })
  // What is true right now: position, weather, moon. Filled in asynchronously;
  // the app is fully usable before it arrives.
  const [context, setContext] = useState<RuntimeContext>({
    hour: new Date().getHours(),
  })
  // Session history drives category rotation and prevents repeats.
  const history = useRef<{ categories: string[]; ids: string[] }>({
    categories: [],
    ids: [],
  })
  const timers = useRef<number[]>([])
  // Draws are unlimited — there's no "energy" cap.

  // Clear any pending timeouts so unmounting mid-animation can't set state later.
  useEffect(() => () => timers.current.forEach(clearTimeout), [])

  // Browsers freeze animation clocks in background tabs. An entrance animation
  // interrupted that way can leave content stuck at opacity:0 after the user
  // returns, so finish any pending finite animations on re-show.
  useEffect(() => {
    const onVisible = () => {
      if (document.hidden) return
      document.querySelectorAll('.rise, .card-in, .badge, .swap').forEach((node) => {
        node.getAnimations?.().forEach((animation) => {
          if (animation.effect?.getTiming().iterations !== Infinity) {
            animation.finish()
          }
        })
      })
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])

  useEffect(() => {
    let cancelled = false
    // Ask the backend for this profile's pool (it applies the offline-built,
    // profile-weighted recommend_pool). The client passes its saved 我的
    // profile so the server can pick the right bucket; fetchPool falls back to
    // the bundled static JSON if the API is unreachable, so the app always
    // has something to draw. ?region=dolomites still selects the region.
    const saved = loadProfile()
    fetchPool({
      region: currentRegion(),
      mbti: saved.mbti,
      preferences: saved.preferences,
    })
      .then((items) => {
        if (!cancelled) setPool(items)
      })
      .catch(() => {
        if (!cancelled) setPool([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Warm the image cache for the pages the user is about to reach. The welcome
  // page shows the small machine + wordmark; the moment they tap 开始扭 we need
  // the big machine and its background. Kicking those off now (they're only
  // tens of KB as WebP) means the machine page paints instantly instead of
  // streaming in. Fire-and-forget; failures are harmless.
  useEffect(() => {
    ;[machineLarge, machineBackground, badgeNormal, badgeSuper].forEach((src) => {
      const img = new Image()
      img.src = src
    })
  }, [])

  // Resolve location and weather once, in the background. We ask on mount
  // rather than at draw time so the permission prompt never interrupts the
  // gacha animation.
  useEffect(() => {
    let cancelled = false
    loadRuntimeContext().then((resolved) => {
      if (!cancelled) setContext(resolved)
    })
    return () => {
      cancelled = true
    }
  }, [])

  /** Pick the next suggestion, honouring category rotation. */
  const pickNext = (): Suggestion | null => {
    // Read the latest profile at draw time so edits on the 我的 page take
    // effect immediately, without threading state through every component.
    const saved = loadProfile()
    const next = drawSuggestion(pool, {
      origin: context.origin,
      hour: context.hour,
      recentCategories: history.current.categories,
      seenIds: history.current.ids,
      profile: {
        mbti: saved.mbti,
        zodiac: saved.zodiac,
        preferences: saved.preferences,
      },
    })
    if (next) {
      history.current.categories.push(next.category)
      history.current.ids.push(next.id)
    }
    return next
  }

  const run = (fn: () => void, delay: number) => {
    timers.current.push(window.setTimeout(fn, delay))
  }

  const goToMachine = () => {
    if (leaving) return
    setLeaving(true)
    run(() => { setPage('machine'); setLeaving(false) }, 320)
  }

  // Full draw sequence: shake -> capsule drops -> capsule opens -> result.
  const draw = () => {
    if (phase !== 'idle') return
    const next = pickNext()
    if (!next) return

    const reveal = () => {
      const rolled = rollLuck()
      setCurrent(next)
      setLuck(rolled)
      setDrawId((id) => id + 1)
      setPage('result')
      setPhase('idle')
      // Log the draw for T+1 analysis; fire-and-forget, never blocks the reveal.
      recordDraw({ id: next.id, luck: rolled.value, isSuper: rolled.isSuper })
    }

    if (prefersReducedMotion()) {
      reveal()
      return
    }

    setPhase('shaking')
    run(() => setPhase('dropping'), SHAKE_MS)
    run(() => setPhase('opening'), SHAKE_MS + DROP_MS)
    run(reveal, SHAKE_MS + DROP_MS + OPEN_MS)
  }

  // Re-draw from the result page: bounce back to the machine, then run the sequence.
  const drawAgain = () => {
    if (phase !== 'idle') return
    if (prefersReducedMotion()) {
      const next = pickNext()
      if (!next) return
      const rolled = rollLuck()
      setCurrent(next)
      setLuck(rolled)
      setDrawId((id) => id + 1)
      recordDraw({ id: next.id, luck: rolled.value, isSuper: rolled.isSuper })
      return
    }
    setLeaving(true)
    run(() => {
      setPage('machine')
      setLeaving(false)
      run(draw, 260)
    }, 300)
  }

  const reset = () => {
    timers.current.forEach(clearTimeout)
    timers.current = []
    setPhase('idle')
    setLeaving(false)
    setDrawId(0)
    setCurrent(null)
    setLuck({ value: 0, isSuper: false })
    history.current = { categories: [], ids: [] }
    setPage('welcome')
  }

  // The artboard is letterboxed on phones; fill that space with the *current*
  // page's own tone so the padding never looks like the wrong (welcome) page.
  const stageTone =
    tab === 'profile'
      ? 'stage-profile'
      : page === 'result'
        ? luck.isSuper
          ? 'stage-result-super'
          : 'stage-result'
        : page === 'machine'
          ? 'stage-machine'
          : 'stage-welcome'

  return (
    <div className={`stage ${stageTone}`}>
      <div className="device">
        {tab === 'main' ? (
          <div className={`swap ${leaving ? 'leaving' : ''}`} key={drawId + '-' + page}>
            {page === 'welcome' && <Welcome onStart={goToMachine} />}
            {page === 'machine' && (
              <Machine
                phase={phase}
                onDraw={draw}
                ready={pool.length > 0}
              />
            )}
            {page === 'result' && (
              <Result
                item={current}
                luck={luck}
                onChange={drawAgain}
                origin={context.origin}
                locationSource={context.locationSource}
              />
            )}
          </div>
        ) : (
          <div className="swap" key="profile">
            <Profile />
          </div>
        )}
      </div>

      {/* Kept outside .device so the scale transform on the artboard does not
          shrink it: the tab bar is pinned to the real viewport bottom and the
          我的 entry is always reachable. */}
      <nav className="tabbar">
        <button
          type="button"
          className={`tab ${tab === 'main' ? 'on' : ''}`}
          onClick={() => {
            // From 我的 -> just switch back. When already on 首页, tapping it
            // restarts the flow from the welcome page (replaces the old ↻).
            if (tab === 'main') reset()
            else setTab('main')
          }}
        >
          <svg viewBox="0 0 24 24" className="tab-ico"><path d="M3 11.5 12 4l9 7.5" /><path d="M5 10v9h14v-9" /></svg>
          <span>首页</span>
        </button>
        <button
          type="button"
          className={`tab ${tab === 'profile' ? 'on' : ''}`}
          onClick={() => setTab('profile')}
        >
          <svg viewBox="0 0 24 24" className="tab-ico"><circle cx="12" cy="8" r="4" /><path d="M4 20c0-4 4-6 8-6s8 2 8 6" /></svg>
          <span>我的</span>
        </button>
      </nav>
    </div>
  )
}

function Welcome({ onStart }: { onStart: () => void }) {
  return (
    <section className="page welcome">
      <span className="tag-note">Page 1 Welcome</span>
      <h1 className="wordmark rise" aria-label="下一站扭蛋">
        <FadeImg className="wordmark-img" src={wordmark} alt="下一站扭蛋" />
      </h1>
      <div className="machine-slot rise d1">
        <i className="deco star-a" /><i className="deco star-b" /><i className="deco star-c" /><i className="deco star-d" />
        <FadeImg className="machine-small float" src={machineSmall} alt="下一站扭蛋机" />
      </div>
      <p className="lead-line rise d2">天灵灵地灵灵，</p>
      <p className="lead-line second rise d3">我们P人要出行！</p>
      <button type="button" className="cta rise d4 pulse" onClick={onStart}>开始扭</button>
    </section>
  )
}

function Machine({
  phase,
  onDraw,
  ready,
}: {
  phase: Phase
  onDraw: () => void
  ready: boolean
}) {
  const busy = phase !== 'idle'
  return (
    <section
      className="page machine"
      style={{ backgroundImage: `url(${machineBackground})` }}
    >
      <span className="tag-note">Page 2 Gachapon</span>

      <i className="deco star-a" aria-hidden="true" />
      <i className="deco star-b" aria-hidden="true" />
      <i className="deco star-c" aria-hidden="true" />
      <i className="deco star-d" aria-hidden="true" />
      <i className="deco star-e" aria-hidden="true" />

      <button
        type="button"
        className={`machine-hit ${phase === 'shaking' ? 'shake' : ''}`}
        onClick={onDraw}
        disabled={busy || !ready}
        aria-label="扭一扭"
      >
        <FadeImg className={`machine-large ${busy ? '' : 'float'}`} src={machineLarge} alt="下一站扭蛋机" />
      </button>

      {(phase === 'dropping' || phase === 'opening') && (
        <div className={`prize ${phase === 'opening' ? 'open' : 'drop'}`} aria-hidden="true">
          <span className="burst" />
          <span className="half top">
            <span className="shine" />
          </span>
          <span className="half bottom" />
        </div>
      )}

      <p className="act-title">{busy ? '正在扭…' : '扭 一 扭'}</p>
      <p className="act-sub">
        {busy ? '好运正在路上' : ready ? '摇动手机' : '正在装填扭蛋…'}
      </p>
    </section>
  )
}

function Result({
  item,
  luck,
  onChange,
  origin,
  locationSource,
}: {
  item: Suggestion | null
  /** Freshly rolled luck for this reveal. */
  luck: Luck
  onChange: () => void
  /** Origin used for distance/direction (always set once context loads). */
  origin?: { lng: number; lat: number }
  /** Where the origin came from; 'default' means we fell back to 恒电大厦. */
  locationSource?: RuntimeContext['locationSource']
}) {
  const shown = useCountUp(luck.value, 760)

  if (!item) {
    return (
      <section className="page result">
        <span className="tag-note dark">Page 3 Result</span>
        <article className="sheet card-in">
          <h2 className="headline">还没有扭出东西</h2>
          <p className="body">回上一页，先扭一下扭蛋机。</p>
        </article>
      </section>
    )
  }

  const navigate = () => {
    // The region decides which map family is correct (see navigationUrl):
    // domestic GCJ-02 data -> AMap; overseas WGS-84 data -> Apple/Google.
    const url = navigationUrl(item, currentRegion())
    const opened = window.open(url, '_blank', 'noopener,noreferrer')
    // Popup blockers can null the handle; fall back to a same-tab navigation
    // so the button never silently does nothing.
    if (!opened) window.location.href = url
  }
  // This is the suggested *dwell* time (how long to spend there), not travel
  // time. Label it explicitly so it is not mistaken for a commute estimate.
  const durationLabel =
    item.durationMinutes >= 60
      ? `建议停留${Math.round((item.durationMinutes / 60) * 10) / 10}小时`
      : `建议停留${item.durationMinutes}分钟`

  // A real fix (amap/gps) lets us state a real distance and direction. When we
  // only have the default origin, we neither show a distance chip nor recompute
  // the direction - that would be a guess dressed up as a fact. In that case we
  // keep the build-time `direction` and add a quiet "location unknown" hint.
  const hasRealFix = !!origin && locationSource !== 'default'

  const distanceLabel =
    hasRealFix && origin
      ? (() => {
          const km = distanceKm(origin, item)
          return km < 1 ? `${Math.round(km * 1000)}m` : `${km.toFixed(1)}km`
        })()
      : null

  const directionLabel =
    hasRealFix && origin ? bearingDirection(origin, item) : item.direction

  return (
    <section className={`page result ${luck.isSuper ? 'super' : ''}`}>
      <span className="tag-note dark">Page 3 Result</span>
      <i className="orb orb-a drift" /><i className="orb orb-b drift s" /><i className="orb orb-c drift l" />
      <i className="glow g1 twinkle" /><i className="glow g2 twinkle b" /><i className="glow g3 twinkle c" />
      <div className={`badge ${luck.isSuper ? 'super' : ''}`}>
        <img
          className="badge-img"
          src={luck.isSuper ? badgeSuper : badgeNormal}
          alt={luck.isSuper ? '超级幸运' : '幸运'}
        />
        <b className="badge-num">+{shown}</b>
      </div>
      <article className="sheet card-in">
        <div className="chips">
          <span className="chip blue rise d1">{item.area}</span>
          <span className="chip green rise d2">{durationLabel}</span>
          {distanceLabel && (
            <span className="chip lilac rise d2">{distanceLabel}</span>
          )}
          <span className="chip peach rise d3">{item.categoryLabel.split(' / ')[0]}</span>
        </div>
        <h2 className="headline rise d2">{item.name}</h2>
        <p className="meta rise d3"><svg viewBox="0 0 24 24" className="pin"><path d="M12 2a7 7 0 0 0-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6a2.5 2.5 0 0 1 0 5.5z" /></svg>{directionLabel}方向 · {item.hook}</p>
        {!hasRealFix && (
          <p className="loc-hint rise d3">未获取到位置，按默认区域推荐</p>
        )}
        <p className="body rise d4">{item.reason}</p>
        <p className="oracle-line rise d4">「{item.oracle}」</p>
        <div className="row rise d5">
          <button type="button" className="ghost" onClick={onChange}>
            <svg viewBox="0 0 24 24" className="ico spin-on-hover"><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 5v6h-6" /></svg>换一个
          </button>
          <button type="button" className="solid" onClick={navigate}>
            <svg viewBox="0 0 24 24" className="ico fill"><path d="M21 3 3 10.5l7 2.8 2.8 7L21 3z" /></svg>导航过去
          </button>
        </div>
      </article>
    </section>
  )
}

function Profile() {
  const [data, setData] = useState<ProfileData>(loadProfile)
  const [saved, setSaved] = useState(false)
  const savedTimer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(savedTimer.current), [])

  const togglePreference = (id: string) => {
    setSaved(false)
    setData((prev) => ({
      ...prev,
      preferences: prev.preferences.includes(id)
        ? prev.preferences.filter((p) => p !== id)
        : [...prev.preferences, id],
    }))
  }

  const save = () => {
    try {
      localStorage.setItem(PROFILE_KEY, JSON.stringify(data))
    } catch {
      // storage may be unavailable (private mode); the in-memory state still holds.
    }
    // Sync to the backend so the T+1 job can build this profile's pool.
    // Fire-and-forget: the local save above is what the UI relies on.
    saveProfile(data)
    setSaved(true)
    window.clearTimeout(savedTimer.current)
    savedTimer.current = window.setTimeout(() => setSaved(false), 1800)
  }

  return (
    <section className="page profile">
      <i className="orb orb-a drift" aria-hidden="true" />
      <i className="orb orb-b drift s" aria-hidden="true" />
      <i className="orb orb-c drift l" aria-hidden="true" />
      <i className="glow g1 twinkle" aria-hidden="true" />
      <i className="glow g2 twinkle b" aria-hidden="true" />
      <i className="glow g3 twinkle c" aria-hidden="true" />

      <h1 className="profile-title rise" aria-label="我的">
        <img className="profile-title-img" src={profileTitle} alt="我的" />
      </h1>

      <div className="pcard pcard-compact rise d1">
        <img className="picon" src={iconMbti} alt="" aria-hidden="true" />
        <div className="pcard-compact-body">
          <h2 className="pcard-title">MBTI人格类型</h2>
          <div className={`pselect blue ${data.mbti ? 'filled' : ''}`}>
            <select
              aria-label="选择你的MBTI"
              value={data.mbti}
              onChange={(e) => { setSaved(false); setData((p) => ({ ...p, mbti: e.target.value })) }}
            >
              <option value="">请选择你的MBTI</option>
              {MBTI_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <svg viewBox="0 0 24 24" className="pselect-caret"><path d="m6 9 6 6 6-6" /></svg>
          </div>
        </div>
      </div>

      <div className="pcard pcard-compact rise d2">
        <img className="picon" src={iconZodiac} alt="" aria-hidden="true" />
        <div className="pcard-compact-body">
          <h2 className="pcard-title">我的星座</h2>
          <div className={`pselect green ${data.zodiac ? 'filled' : ''}`}>
            <select
              aria-label="选择星座"
              value={data.zodiac}
              onChange={(e) => { setSaved(false); setData((p) => ({ ...p, zodiac: e.target.value })) }}
            >
              <option value="">请选择星座</option>
              {ZODIAC_SIGNS.map((z) => (
                <option key={z} value={z}>{z}</option>
              ))}
            </select>
            <svg viewBox="0 0 24 24" className="pselect-caret"><path d="m6 9 6 6 6-6" /></svg>
          </div>
        </div>
      </div>

      <div className="pcard pcard-pref rise d3">
        <img className="picon" src={iconHeart} alt="" aria-hidden="true" />
        <div className="pref-body">
          <h2 className="pcard-title">今日偏好</h2>
          <div className="pref-row">
            {PREFERENCES.map((pref) => {
              const active = data.preferences.includes(pref.id)
              return (
                <button
                  key={pref.id}
                  type="button"
                  className={`pref-pill ${pref.color} ${active ? 'active' : ''}`}
                  aria-pressed={active}
                  onClick={() => togglePreference(pref.id)}
                >
                  {pref.label}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      <button type="button" className={`save-btn rise d4 ${saved ? 'done' : ''}`} onClick={save}>
        {saved ? '已保存 ✓' : '保存设置'}
      </button>
    </section>
  )
}

/**
 * An <img> that fades in once decoded, so a slow network shows a clean
 * empty space that resolves into the full picture — never a half-painted,
 * top-to-bottom "streaming" image. Falls back to showing immediately for
 * cached images (onLoad fires synchronously) and when motion is reduced.
 */
function FadeImg({
  className = '',
  style,
  ...rest
}: ImgHTMLAttributes<HTMLImageElement>) {
  const [loaded, setLoaded] = useState(false)
  return (
    <img
      {...rest}
      className={className}
      onLoad={() => setLoaded(true)}
      style={{
        ...style,
        opacity: loaded ? 1 : 0,
        transition: prefersReducedMotion() ? undefined : 'opacity .34s ease',
      }}
    />
  )
}

/** Animate a number from 0 to `target` with an ease-out curve. */
function useCountUp(target: number, duration: number) {
  const [value, setValue] = useState(prefersReducedMotion() ? target : 0)

  useEffect(() => {
    if (prefersReducedMotion()) { setValue(target); return }
    const steps = 30
    const interval = duration / steps
    let i = 0
    const id = window.setInterval(() => {
      i++
      const progress = i / steps
      const eased = 1 - Math.pow(1 - progress, 3)
      setValue(Math.round(target * eased))
      if (i >= steps) {
        window.clearInterval(id)
        setValue(target)
      }
    }, interval)
    return () => window.clearInterval(id)
  }, [target, duration])

  return value
}

export default App
