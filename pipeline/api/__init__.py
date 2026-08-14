"""HTTP service layer for 下一站扭蛋.

A thin FastAPI shell over the existing DB layer (pipeline/db). It does not
re-implement any product logic — it serves the offline-built recommend pool,
records draws, stores profiles, and reports health. The gacha "feel" (category
rotation, session de-dup, weighted pick) deliberately stays in the frontend
(src/gacha.ts); the server's job is only to hand over a good pool and to log.
"""
