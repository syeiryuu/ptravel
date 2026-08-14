import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

const run = promisify(execFile)

/**
 * Dev-only weather endpoint.
 *
 * The QWeather credential must never reach the browser bundle, so the client
 * calls our own `/api/conditions` instead. In development that is served here;
 * in production this same path should be backed by a real serverless function
 * (the response shape is the contract).
 *
 * Implemented by shelling out to the pipeline's Python client rather than
 * reimplementing the Ed25519 JWT signing in TypeScript - one implementation,
 * one place to fix.
 */
function weatherEndpoint(): Plugin {
  // Conditions change slowly; caching keeps us far inside the free quota
  // even with the app open and reloading.
  let cache: { at: number; body: string } | null = null
  const TTL_MS = 10 * 60 * 1000

  return {
    name: 'lucky-gacha-weather',
    configureServer(server) {
      server.middlewares.use('/api/conditions', async (_req, res) => {
        res.setHeader('Content-Type', 'application/json; charset=utf-8')

        if (cache && Date.now() - cache.at < TTL_MS) {
          res.end(cache.body)
          return
        }
        try {
          const { stdout } = await run(
            'python3',
            ['pipeline/conditions.py'],
            { cwd: process.cwd(), timeout: 15_000 },
          )
          cache = { at: Date.now(), body: stdout.trim() || '{}' }
          res.end(cache.body)
        } catch {
          // No key configured, no python, no network - all mean the same thing
          // to the client: carry on without weather.
          res.end('{}')
        }
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  // Relative base so the built bundle works no matter what sub-path it is
  // served from — GitHub Pages puts a project site under /<repo>/, and a
  // relative base means we don't have to hard-code the repo name here.
  base: './',
  plugins: [react(), weatherEndpoint()],
  server: {
    // Forward the app's data/behaviour API to the FastAPI service, while
    // leaving /api/conditions to the local weather middleware above (bypass
    // returns the original path so Vite serves it in-process instead of
    // proxying it to a backend that has no such route).
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
        bypass(req) {
          if (req.url?.startsWith('/api/conditions')) return req.url
        },
      },
    },
  },
})
