// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure time-filtering logic for the map timebar (A9) + timelapse (A8).
// The histogram is built from the RENDERED entities' created_at (always in
// sync with what's on screen — no extra fetch). Undated entities are never
// hidden by time (honest degradation: "we don't know when this was born").

export interface TimeHistogramBucket {
  /** Bucket start / end in epoch ms. */
  t0: number
  t1: number
  count: number
}

export interface TimeHistogram {
  minMs: number
  maxMs: number
  buckets: TimeHistogramBucket[]
}

/** A brush window and/or a playback cursor (epoch ms). null = inactive. */
export interface TimeFilter {
  window: [number, number] | null
  cursor: number | null
}

export type TimeNodeState = "visible" | "dim" | "hidden"

export function parseCreatedAt(iso: string | null | undefined): number | null {
  if (!iso) return null
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? null : ms
}

export function buildTimeHistogram(
  entities: readonly { created_at?: string | null }[],
  bucketCount: number,
): TimeHistogram | null {
  const times: number[] = []
  for (const e of entities) {
    const ms = parseCreatedAt(e.created_at)
    if (ms !== null) times.push(ms)
  }
  if (times.length === 0) return null
  let minMs = Infinity
  let maxMs = -Infinity
  for (const t of times) {
    if (t < minMs) minMs = t
    if (t > maxMs) maxMs = t
  }
  const n = Math.max(1, bucketCount)
  // Guard a zero-width span (all entities share a timestamp) so bucketing
  // doesn't divide by zero; the single bucket then holds everything.
  const span = maxMs - minMs || 1
  const buckets: TimeHistogramBucket[] = Array.from({ length: n }, (_, i) => ({
    t0: minMs + (span * i) / n,
    t1: minMs + (span * (i + 1)) / n,
    count: 0,
  }))
  for (const t of times) {
    const idx = Math.min(n - 1, Math.floor(((t - minMs) / span) * n))
    buckets[idx].count++
  }
  return { minMs, maxMs, buckets }
}

/**
 * Ids of entities born in the half-open interval (prevMs, nowMs] — the set
 * that just crossed the playback cursor this step (A8), which the map pulses
 * via the existing pulseMap machinery. `prevMs = null` means "from the dawn of
 * time" (the first playback step). Undated/garbage created_at never appears.
 * `cap` bounds the ripple deterministically (entity order) so a dense step
 * doesn't flood the scene with rings.
 */
export function bornBetween(
  entities: readonly { id: string; created_at?: string | null }[],
  prevMs: number | null,
  nowMs: number,
  cap = 32,
): string[] {
  const out: string[] = []
  for (const e of entities) {
    const ms = parseCreatedAt(e.created_at)
    if (ms === null) continue
    if ((prevMs === null || ms > prevMs) && ms <= nowMs) {
      out.push(e.id)
      if (out.length >= cap) break
    }
  }
  return out
}

/**
 * Resolve a node's render state under the active time filter. Playback (cursor)
 * takes precedence over the brush window: not-yet-born nodes hide; born nodes
 * then obey the window (out-of-window dims but stays as context).
 */
export function timeNodeState(
  createdAt: string | null | undefined,
  filter: TimeFilter | null,
): TimeNodeState {
  if (!filter || (filter.window === null && filter.cursor === null)) return "visible"
  const ms = parseCreatedAt(createdAt)
  if (ms === null) return "visible" // undated → always shown
  if (filter.cursor !== null && ms > filter.cursor) return "hidden"
  if (filter.window !== null && (ms < filter.window[0] || ms > filter.window[1])) return "dim"
  return "visible"
}
