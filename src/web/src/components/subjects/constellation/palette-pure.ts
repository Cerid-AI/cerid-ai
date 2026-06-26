// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure, dependency-free palette helpers. No WebGL, no React, no sigma.
// Extracted here so they can be unit-tested without triggering the
// @/lib/graph/identity → community-layer dependency chain.
//
// Consumers: palette.ts re-exports all of these; import from palette.ts
// in production code and from this file ONLY in unit tests.

export const COMMUNITY_PALETTE_RGB = [
  [0.898, 0.518, 0.478], [0.898, 0.659, 0.478], [0.898, 0.784, 0.478], [0.831, 0.686, 0.216],
  [0.784, 0.898, 0.478], [0.659, 0.898, 0.478], [0.478, 0.898, 0.784], [0.478, 0.784, 0.898],
  [0.478, 0.659, 0.898], [0.659, 0.478, 0.898], [0.784, 0.478, 0.898], [0.898, 0.478, 0.784],
] as const

export const GRAPHITE: readonly [number, number, number] = [0.36, 0.40, 0.50]

/** Sentinel community_id marking isolated (unplaced) nodes. */
export const ISOLATED_COMMUNITY_ID = "isolated"

export function communityRgb(communityId: string | null): readonly [number, number, number] {
  if (!communityId || communityId === ISOLATED_COMMUNITY_ID) return GRAPHITE
  let h = 0
  for (let i = 0; i < communityId.length; i++) {
    h = ((h << 5) - h) + communityId.charCodeAt(i)
    h |= 0
  }
  const idx = Math.abs(h) % COMMUNITY_PALETTE_RGB.length
  return COMMUNITY_PALETTE_RGB[idx]
}

/**
 * Base fill alpha derived from mention_count.
 *
 * Single-mention nodes are newly-observed or rarely-cited — render softer
 * so their "established-ness" reads visually. Well-cited nodes are fully
 * opaque. Range: [0.55, 1.0].
 *
 * Composes with (but does not replace) focus-dim and type-filter multipliers.
 */
export function nodeBaseAlpha(mentionCount: number): number {
  const mc = Math.max(0, mentionCount)
  // log1p concave curve: mc=1→~0.61, mc=5→~0.74, mc=25→~0.90, mc=100→1.0
  return Math.min(1.0, 0.55 + Math.log1p(mc) / Math.log1p(100) * 0.45)
}
