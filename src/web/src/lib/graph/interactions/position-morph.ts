// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Renderer-agnostic in-place attribute tween over a graphology graph
// (Graph Living-Map S2). One rAF loop drives ALL nodes — never one loop
// per node — so layout morphs stay cheap at 10k nodes. Positions are
// updated in place; the sigma instance is NEVER reconstructed (the v3
// node-program bug). Callers refresh with { skipIndexation: true } per
// frame via onFrame and do a full refresh on settle via onDone.
//
// Consumed by: A5 Atlas ego re-centering, A6 map layout-preset morphs,
// A10 combo expand.

/** Structural subset of graphology's Graph the morph engine needs. */
export interface MorphableGraph {
  hasNode(id: string): boolean
  getNodeAttribute(id: string, attr: string): unknown
  setNodeAttribute(id: string, attr: string, value: number): void
}

export type MorphTargets = Map<string, Record<string, number>>

export interface MorphOptions {
  durationMs?: number
  ease?: (t: number) => number
  /** Snap to targets in a single synchronous frame (a11y contract). */
  reducedMotion?: boolean
  /** Called once per animation frame after attributes are written. */
  onFrame?: () => void
  /** Called exactly once when all targets are reached (not on cancel). */
  onDone?: () => void
  // Injectable clock/scheduler for deterministic tests.
  raf?: (cb: FrameRequestCallback) => number
  cancelRaf?: (handle: number) => void
  now?: () => number
}

export interface MorphHandle {
  cancel(): void
}

export function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}

/** Pure per-frame interpolation over every numeric key — exported for tests. */
export function interpolateFrame(
  from: Record<string, number>,
  to: Record<string, number>,
  t: number,
  ease: (t: number) => number,
): Record<string, number> {
  const e = ease(t)
  const out: Record<string, number> = {}
  for (const key of Object.keys(to)) {
    const a = from[key] ?? to[key]
    out[key] = a + (to[key] - a) * e
  }
  return out
}

/**
 * Tween the given nodes' numeric attributes (x/y/size/…) to their targets
 * in place. Returns a handle whose cancel() halts the tween mid-flight
 * (attributes stay wherever they were — callers decide what happens next).
 */
export function morphPositions(
  graph: MorphableGraph,
  targets: MorphTargets,
  opts: MorphOptions = {},
): MorphHandle {
  const {
    durationMs = 600,
    ease = easeOutCubic,
    reducedMotion = false,
    onFrame,
    onDone,
    raf = requestAnimationFrame,
    cancelRaf = cancelAnimationFrame,
    now = () => performance.now(),
  } = opts

  // Snapshot starting values for ids present in the graph.
  const entries: Array<{ id: string; from: Record<string, number>; to: Record<string, number> }> = []
  for (const [id, to] of targets) {
    if (!graph.hasNode(id)) continue
    const from: Record<string, number> = {}
    for (const key of Object.keys(to)) {
      const v = graph.getNodeAttribute(id, key)
      from[key] = typeof v === "number" ? v : to[key]
    }
    entries.push({ id, from, to })
  }

  const writeAll = (t: number) => {
    for (const { id, from, to } of entries) {
      const frame = interpolateFrame(from, to, t, ease)
      for (const key of Object.keys(frame)) {
        graph.setNodeAttribute(id, key, frame[key])
      }
    }
  }

  if (reducedMotion || entries.length === 0) {
    writeAll(1)
    onFrame?.()
    onDone?.()
    return { cancel: () => {} }
  }

  const start = now()
  let handle: number | null = null
  let cancelled = false

  const step = () => {
    if (cancelled) return
    const t = Math.min(1, (now() - start) / durationMs)
    writeAll(t)
    onFrame?.()
    if (t >= 1) {
      handle = null
      onDone?.()
      return
    }
    handle = raf(step)
  }
  handle = raf(step)

  return {
    cancel: () => {
      cancelled = true
      if (handle !== null) {
        cancelRaf(handle)
        handle = null
      }
    },
  }
}
