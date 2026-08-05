// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Bridges lens (C1) — score→color mapping. Betweenness centrality (which nodes
// sit on the most shortest paths between clusters — the graph's connectors) is
// computed off-thread in graph-metrics.worker.ts; this module turns the raw
// scores into a two-stop dim→interaction ramp. Pure, so it's unit-testable
// without a worker or WebGL.

/** Rescale a betweenness map so its max is 1 (relative importance). All-zero
 *  and empty inputs pass through unchanged (no divide-by-zero). */
export function normalizeScores(scores: Record<string, number>): Record<string, number> {
  let max = 0
  for (const v of Object.values(scores)) if (v > max) max = v
  if (max <= 0) return scores
  const out: Record<string, number> = {}
  for (const k in scores) out[k] = scores[k] / max
  return out
}

function parseHex(hex: string): [number, number, number] {
  const h = hex.replace("#", "")
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}

function toHex(n: number): string {
  return Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, "0")
}

/** Per-channel linear interpolation between two #rrggbb hex colors. */
export function lerpHex(aHex: string, bHex: string, t: number): string {
  const k = Math.min(1, Math.max(0, t))
  const a = parseHex(aHex)
  const b = parseHex(bHex)
  return `#${toHex(a[0] + (b[0] - a[0]) * k)}${toHex(a[1] + (b[1] - a[1]) * k)}${toHex(a[2] + (b[2] - a[2]) * k)}`
}

/**
 * Color for a normalized betweenness score (0..1): a dim→interaction ramp with
 * a sqrt gamma so the handful of true bridges light up before the very top of
 * the range, instead of everything reading as near-dim.
 */
export function bridgesColor(score01: number, dimHex: string, interactionHex: string): string {
  const clamped = Math.min(1, Math.max(0, score01))
  return lerpHex(dimHex, interactionHex, Math.sqrt(clamped))
}
