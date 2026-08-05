// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Lens-switch color-tween math (B6). When the color lens changes (clusters →
// trust → type → domain, or a theme swap), the instanced nodes lerp from their
// previous per-instance color to the new one over a short window instead of
// snapping. Pure helpers, extracted so the timing + interpolation can be
// unit-tested without WebGL.

/** Color-tween duration in seconds. Matches the plan's 400ms lens crossfade. */
export const COLOR_TWEEN_S = 0.4

/**
 * Clamped tween progress in [0, 1] for a color lerp that began at `startS`
 * (both times on the same shader clock). A zero/negative duration snaps
 * immediately to 1 (used for reduced-motion / no-tween).
 */
export function colorTweenK(nowS: number, startS: number, durationS = COLOR_TWEEN_S): number {
  if (durationS <= 0) return 1
  return Math.min(1, Math.max(0, (nowS - startS) / durationS))
}

/** Linear interpolation of a single color channel. */
export function mixChannel(from: number, to: number, k: number): number {
  return from + (to - from) * k
}
