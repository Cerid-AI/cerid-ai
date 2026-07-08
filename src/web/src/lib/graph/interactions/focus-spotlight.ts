// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Renderer-agnostic hover/pin spotlight controller (Graph Living-Map S1).
// Extracted from CartographerMap's focusNeighborsRef/rampFocusProgress so
// Atlas and the map share one implementation: a focus center spotlights
// its 1-hop neighborhood while everything else eases out (hue-preserving
// alpha fade + gentle shrink) over ~180ms instead of snapping.
//
// The controller owns the eased progress ramp; reducers READ progress()
// and neighbors() per refresh — they are never reinstalled on hover.

export interface FocusSpotlightOptions {
  getNeighbors(id: string): Iterable<string>
  hasNode(id: string): boolean
  /** Cheap repaint, e.g. () => sigma.refresh({ skipIndexation: true }). */
  refresh(): void
  reducedMotion?: boolean
  fadeMs?: number
  // Injectable clock/scheduler for deterministic tests.
  raf?: (cb: FrameRequestCallback) => number
  cancelRaf?: (handle: number) => void
  now?: () => number
}

export interface FocusSpotlight {
  /** Set the hover/pin center; null (or an unknown id) fades the focus out. */
  setCenter(id: string | null): void
  center(): string | null
  /** Eased focus strength 0..1 read by reducers each refresh. */
  progress(): number
  /** Center + 1-hop set, or null once a fade-out completes. */
  neighbors(): Set<string> | null
  dispose(): void
}

/** Non-neighbor alpha under focus: hue-preserving fade to 20% at full focus. */
export function focusNodeAlpha(baseAlpha: number, progress: number): number {
  return baseAlpha * (1 - 0.8 * progress)
}

/** Non-neighbor size under focus: shrink up to 40%, clamped to the hit floor. */
export function focusNodeSize(size: number, progress: number, minSize: number): number {
  return Math.max(minSize, size * (1 - 0.4 * progress))
}

export function createFocusSpotlight(opts: FocusSpotlightOptions): FocusSpotlight {
  const {
    getNeighbors,
    hasNode,
    refresh,
    reducedMotion = false,
    fadeMs = 180,
    raf = requestAnimationFrame,
    cancelRaf = cancelAnimationFrame,
    now = () => performance.now(),
  } = opts

  let centerId: string | null = null
  let neighborSet: Set<string> | null = null
  let progress = 0
  let rafHandle: number | null = null

  function cancelRamp(): void {
    if (rafHandle !== null) {
      cancelRaf(rafHandle)
      rafHandle = null
    }
  }

  function ramp(target: number): void {
    cancelRamp()
    if (reducedMotion) {
      progress = target
      if (target === 0) neighborSet = null
      refresh()
      return
    }
    const start = now()
    const from = progress
    const step = () => {
      const t = Math.min(1, (now() - start) / fadeMs)
      // easeOutCubic for a soft settle (same curve as the map's original ramp)
      const e = 1 - Math.pow(1 - t, 3)
      progress = from + (target - from) * e
      if (t >= 1) {
        rafHandle = null
        // Clear the held neighborhood only AFTER the fade-out completes so
        // non-focus nodes ease back to full rather than snapping.
        if (target === 0) neighborSet = null
      } else {
        rafHandle = raf(step)
      }
      refresh()
    }
    rafHandle = raf(step)
  }

  return {
    setCenter(id: string | null): void {
      if (id !== null && hasNode(id)) {
        centerId = id
        const set = new Set<string>()
        for (const n of getNeighbors(id)) set.add(n)
        set.add(id)
        neighborSet = set
        ramp(1)
      } else {
        centerId = null
        // Keep the prior neighborhood until the fade-out finishes (ramp
        // clears it at progress 0) so the un-dim is smooth.
        ramp(0)
      }
    },
    center: () => centerId,
    progress: () => progress,
    neighbors: () => neighborSet,
    dispose(): void {
      cancelRamp()
    },
  }
}
