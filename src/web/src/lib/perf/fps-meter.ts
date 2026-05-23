// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// FPS meter — measures rendering frame rate with a rolling window.
// Lightweight enough to leave running in the perf harness without
// distorting the measurement. Used by both Atlas (Phase A) and
// Constellation (Phase B) perf budgets.

export interface FpsStats {
  /** Frames in the last window */
  frames: number
  /** Average FPS over the window (rounded to 1 decimal) */
  avgFps: number
  /** Min/max instantaneous FPS observed in the window */
  minFps: number
  maxFps: number
  /** Wall-clock window length (ms) */
  windowMs: number
  /** Number of completed windows so far (debugging) */
  windowsCompleted: number
}

export interface FpsMeterOptions {
  /** Rolling window length in ms. Default 1000 (1s). */
  windowMs?: number
  /** Optional callback fired after each completed window. */
  onWindow?: (stats: FpsStats) => void
}

export interface FpsMeterHandle {
  /** Last completed window's stats. Null until the first window finishes. */
  current(): FpsStats | null
  /** Mark a frame. Call from your render loop or RAF tick. */
  tick(): void
  /** Stop measuring + clean up. */
  stop(): void
}

export function createFpsMeter({ windowMs = 1000, onWindow }: FpsMeterOptions = {}): FpsMeterHandle {
  let frames = 0
  let windowStart = performance.now()
  let lastFrameTime = windowStart
  let minDelta = Infinity
  let maxDelta = 0
  let latest: FpsStats | null = null
  let windowsCompleted = 0
  let stopped = false

  return {
    current: () => latest,
    tick: () => {
      if (stopped) return
      const now = performance.now()
      const delta = now - lastFrameTime
      if (delta > 0) {
        if (delta < minDelta) minDelta = delta
        if (delta > maxDelta) maxDelta = delta
      }
      lastFrameTime = now
      frames++

      const elapsed = now - windowStart
      if (elapsed >= windowMs) {
        const avg = (frames * 1000) / elapsed
        const max = minDelta > 0 ? 1000 / minDelta : 0
        const min = maxDelta > 0 ? 1000 / maxDelta : 0
        windowsCompleted++
        latest = {
          frames,
          avgFps: Math.round(avg * 10) / 10,
          minFps: Math.round(min * 10) / 10,
          maxFps: Math.round(max * 10) / 10,
          windowMs: elapsed,
          windowsCompleted,
        }
        onWindow?.(latest)
        // Reset for next window
        frames = 0
        windowStart = now
        minDelta = Infinity
        maxDelta = 0
      }
    },
    stop: () => {
      stopped = true
    },
  }
}
