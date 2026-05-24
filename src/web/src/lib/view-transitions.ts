// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Thin wrapper around the View Transitions API with feature detection.
 *
 * Chrome 111+ and Safari 18+ ship `document.startViewTransition()`,
 * which snapshots the DOM before + after a state change and animates
 * the visual difference automatically. Matching `view-transition-name`
 * CSS properties on both sides produce shared-element morphs — e.g.
 * the focal entity flying from Atlas 2D position to Constellation 3D
 * position when the user swaps modes.
 *
 * Firefox doesn't support this API yet; we fall back to executing the
 * state update directly, and the existing CSS keyframes (`mode-swap`,
 * `mode-swap-deep`) provide a reasonable visual baseline.
 *
 * Also short-circuits on `prefers-reduced-motion`.
 */

interface ViewTransitionAPI {
  startViewTransition: (update: () => void | Promise<void>) => {
    ready: Promise<void>
    finished: Promise<void>
    updateCallbackDone: Promise<void>
    skipTransition: () => void
  }
}

function hasViewTransitions(): boolean {
  if (typeof document === "undefined") return false
  return typeof (document as unknown as ViewTransitionAPI).startViewTransition === "function"
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false
}

/**
 * Run ``update`` inside a view transition when supported, otherwise
 * call it directly. The promise resolves when the transition finishes
 * (or immediately on fallback).
 */
export function withViewTransition(update: () => void | Promise<void>): Promise<void> {
  if (!hasViewTransitions() || prefersReducedMotion()) {
    const result = update()
    return result instanceof Promise ? result : Promise.resolve()
  }
  const transition = (document as unknown as ViewTransitionAPI).startViewTransition(update)
  return transition.finished.catch(() => undefined)
}

/**
 * Apply a `view-transition-name` to an element imperatively (e.g.
 * the focal node in Atlas right before a mode swap). Removes itself
 * after the transition so the name doesn't linger across unrelated
 * state changes.
 */
export function tagForTransition(el: HTMLElement | null, name: string): () => void {
  if (!el || !hasViewTransitions()) return () => undefined
  const prior = el.style.viewTransitionName
  el.style.viewTransitionName = name
  return () => {
    el.style.viewTransitionName = prior
  }
}
