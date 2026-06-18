// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FLIP (First, Last, Invert, Play) — animate layout changes that the
 * browser would otherwise teleport. The caller records positions of a
 * stable set of elements, mutates the DOM, then calls `playFromSnapshot`
 * which inverts each element to its old position and transitions back.
 *
 * Use when reordering, inserting, or removing items from a list — the
 * surviving siblings should slide to their new positions rather than
 * jump. Respects `prefers-reduced-motion`.
 */

interface FlipSnapshot {
  el: HTMLElement
  rect: DOMRect
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false
}

// Track in-flight cleanups per element so a fresh flip on the same node
// can cancel the previous one cleanly. Without this, two overlapping
// `flip()` calls (e.g. a quick double-reorder) race on the inline style
// reset and the first play snaps to its destination mid-flight.
const inFlight = new WeakMap<HTMLElement, () => void>()

/**
 * Capture the current bounding rects of a set of elements. Pair with
 * `playFromSnapshot` after mutating the DOM.
 */
export function snapshotPositions(
  elements: Iterable<HTMLElement | null | undefined>,
): FlipSnapshot[] {
  const snaps: FlipSnapshot[] = []
  for (const el of elements) {
    if (!el) continue
    snaps.push({ el, rect: el.getBoundingClientRect() })
  }
  return snaps
}

/**
 * For each snapshot, compute the delta from the recorded position to the
 * current position, apply the inverse transform (so the element appears
 * to still be in its old spot), then in the next frame remove the
 * transform — the browser interpolates back via the supplied transition.
 *
 * Returns a promise that resolves when the longest transition completes.
 */
export function playFromSnapshot(
  snapshots: FlipSnapshot[],
  { duration = 260, easing = "cubic-bezier(0.16, 1, 0.3, 1)" }: { duration?: number; easing?: string } = {},
): Promise<void> {
  if (prefersReducedMotion() || snapshots.length === 0) return Promise.resolve()

  const work: { el: HTMLElement; dx: number; dy: number }[] = []
  for (const { el, rect } of snapshots) {
    if (!el.isConnected) continue
    const next = el.getBoundingClientRect()
    const dx = rect.left - next.left
    const dy = rect.top - next.top
    if (dx === 0 && dy === 0) continue
    work.push({ el, dx, dy })
  }
  if (work.length === 0) return Promise.resolve()

  // Cancel any in-flight flip on the same elements so the new play wins.
  for (const { el } of work) {
    inFlight.get(el)?.()
  }

  // INVERT
  for (const { el, dx, dy } of work) {
    el.style.transition = "none"
    el.style.transform = `translate(${dx}px, ${dy}px)`
  }

  // Force layout flush so the inverse transform paints before we clear it.
  void work[0].el.offsetHeight

  // PLAY
  return new Promise((resolve) => {
    let cancelled = false
    const timers: number[] = []
    const cleanup = (commit: boolean) => {
      if (cancelled) return
      cancelled = true
      for (const t of timers) window.clearTimeout(t)
      for (const { el } of work) {
        if (inFlight.get(el) === cleanup.bind(null, false)) inFlight.delete(el)
        if (commit) {
          el.style.transition = ""
          el.style.transform = ""
        }
      }
      resolve()
    }
    // Register cleanup as the in-flight cancel handle for each element.
    const cancel = () => cleanup(false)
    for (const { el } of work) inFlight.set(el, cancel)

    requestAnimationFrame(() => {
      if (cancelled) return
      for (const { el } of work) {
        el.style.transition = `transform ${duration}ms ${easing}`
        el.style.transform = ""
      }
      timers.push(window.setTimeout(() => cleanup(true), duration + 16))
    })
  })
}

/**
 * Convenience wrapper: capture, run `mutate`, then play. The mutate
 * callback should perform the DOM/state change that causes the layout
 * shift — for React, it's usually a `flushSync(() => setState(...))`.
 */
export async function flip(
  elements: Iterable<HTMLElement | null | undefined>,
  mutate: () => void | Promise<void>,
  options?: { duration?: number; easing?: string },
): Promise<void> {
  const snaps = snapshotPositions(elements)
  await mutate()
  return playFromSnapshot(snaps, options)
}
