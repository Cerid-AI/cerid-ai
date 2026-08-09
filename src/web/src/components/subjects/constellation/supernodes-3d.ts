// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// 3D community super-nodes: group entities by their ancestor community at a
// Leiden level and place a single node at the members' 3D centroid, sized by
// member count. The hierarchy/edge structure comes from the shared, coordinate-
// agnostic map helpers; only the 3D positions are computed here. Pure — unit-
// testable without three.js.

import { levelForRatio } from "./map/community-hierarchy-levels"

export interface SuperNode3D {
  id: string
  x: number
  y: number
  z: number
  count: number
  radius: number
}

export function superRadius(count: number): number {
  // Cube-root so a 100-member community isn't 100× a singleton; +floor so
  // small communities are still grabbable.
  return 0.6 + Math.cbrt(Math.max(1, count)) * 0.5
}

export function buildSuperNodes3D(
  entities: Array<{ x: number; y: number; z: number; community: string | null }>,
  ancestorAt: (id: string, level: number) => string,
  level: number,
): SuperNode3D[] {
  const groups = new Map<string, { sx: number; sy: number; sz: number; n: number }>()
  for (const e of entities) {
    if (!e.community) continue
    const cid = level <= 0 ? e.community : ancestorAt(e.community, level)
    const g = groups.get(cid) ?? { sx: 0, sy: 0, sz: 0, n: 0 }
    g.sx += e.x; g.sy += e.y; g.sz += e.z; g.n += 1
    groups.set(cid, g)
  }
  const out: SuperNode3D[] = []
  for (const [id, g] of groups) {
    out.push({ id, x: g.sx / g.n, y: g.sy / g.n, z: g.sz / g.n, count: g.n, radius: superRadius(g.n) })
  }
  return out
}

// ---------------------------------------------------------------------------
// Distance-driven collapse LOD — the 3D sibling of the 2D map's
// isCollapsed + levelForRatio pairing (map/community-supernodes.ts +
// map/community-hierarchy-levels.ts). Hysteresis: the graph only collapses
// once the camera crosses COLLAPSE_IN, and only expands again once it drops
// back to COLLAPSE_OUT — the gap between the two thresholds prevents
// flicker when the camera hovers near the boundary.
// ---------------------------------------------------------------------------

/** Camera distance at which the graph collapses to community super-nodes. */
export const COLLAPSE_IN = 40
/** Camera distance at which a collapsed graph expands back to members. Must be < COLLAPSE_IN. */
export const COLLAPSE_OUT = 34
/** Camera-distance span per additional Leiden level once collapsed. */
export const LEVEL_STEP_3D = 6

/**
 * Maps camera distance + the previously active level to the next collapsed
 * level (null = show members). Pure — the caller (a per-frame R3F sampler)
 * owns tracking `prevLevel` across frames and only re-renders React state
 * when the returned value actually changes.
 */
export function collapsedLevelForDistance(
  distance: number,
  prevLevel: number | null,
  maxLevel: number,
  collapseIn: number = COLLAPSE_IN,
  collapseOut: number = COLLAPSE_OUT,
): number | null {
  // No hierarchy loaded yet (maxLevel < 0) — never collapse.
  if (maxLevel < 0) return null
  const wasCollapsed = prevLevel !== null
  if (wasCollapsed) {
    // Already collapsed — only expand once the camera comes back past
    // COLLAPSE_OUT (the lower, hysteresis-narrowed threshold).
    if (distance <= collapseOut) return null
  } else {
    // Still expanded — only collapse once the camera crosses COLLAPSE_IN.
    if (distance < collapseIn) return null
  }
  return levelForRatio(distance, maxLevel + 1, collapseIn, LEVEL_STEP_3D)
}
