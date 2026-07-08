// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure graph-migration helpers for Atlas ego re-centering (Living-Map A5).
// On refocus, the persistent live graph MIGRATES to the next neighborhood
// instead of Atlas killing + reconstructing sigma (the v3 second-
// construction program bug): common nodes tween to warm-started FA2
// positions, entering nodes spawn at a live neighbor and grow in, exiting
// nodes shrink out and are dropped on settle.
//
// All functions are renderer-agnostic and operate on graphology graphs;
// Atlas.tsx orchestrates them around applyLayout + morphPositions.

import type { MorphTargets } from "@/lib/graph/interactions/position-morph"

/** Structural subset of graphology the migration helpers need. */
interface GraphLike {
  hasNode(id: string): boolean
  nodes(): string[]
  edges(): string[]
  neighbors(id: string): string[]
  source(edge: string): string
  target(edge: string): string
  hasEdge(edge: string): boolean
  getNodeAttribute(id: string, attr: string): unknown
  setNodeAttribute(id: string, attr: string, value: unknown): void
  // `object` (not Record) so typed graphology instances — whose attribute
  // interfaces lack an index signature — remain structurally assignable.
  getNodeAttributes(id: string): object
  getEdgeAttributes(edge: string): object
  // graphology declares this member property-style, so params are checked
  // strictly (no method bivariance): `never` keeps every concrete edge-attr
  // type assignable; the single internal call site casts.
  addEdgeWithKey(key: string, source: string, target: string, attrs: never): void
  dropEdge(edge: string): void
}

/** Exit nodes shrink to this size before being dropped (0 breaks sigma's quadtree). */
export const EXIT_SIZE = 0.01

/** Attributes that must never be bulk-copied from a snapshot onto the live graph. */
const NON_SYNC_ATTRS = new Set(["x", "y", "size", "highlighted", "spawnProgress"])

const DEFAULT_JITTER = () => (Math.random() - 0.5) * 0.5

/**
 * Warm-start seeding before FA2: common nodes take their live positions
 * (mental-map stability — FA2 then only relaxes, it doesn't re-roll),
 * entering nodes start at a live neighbor (else the live focal), so the
 * layout grows outward from where the user is looking.
 *
 * Mutates `next` positions in place and returns the spawn position per
 * entering node (Atlas adds them to the live graph there before morphing
 * them out to their final layout position).
 */
export function seedWarmPositions(
  next: GraphLike,
  live: GraphLike,
  focalId: string,
  jitter: () => number = DEFAULT_JITTER,
): Map<string, { x: number; y: number }> {
  const spawns = new Map<string, { x: number; y: number }>()
  const focalPos = live.hasNode(focalId)
    ? { x: live.getNodeAttribute(focalId, "x") as number, y: live.getNodeAttribute(focalId, "y") as number }
    : null

  for (const id of next.nodes()) {
    if (live.hasNode(id)) {
      next.setNodeAttribute(id, "x", live.getNodeAttribute(id, "x"))
      next.setNodeAttribute(id, "y", live.getNodeAttribute(id, "y"))
      continue
    }
    // Entering node: spawn at the first next-graph neighbor that already
    // lives on screen; fall back to the focal; last resort keep target.
    let spawn: { x: number; y: number } | null = null
    for (const nb of next.neighbors(id)) {
      if (live.hasNode(nb)) {
        spawn = {
          x: live.getNodeAttribute(nb, "x") as number,
          y: live.getNodeAttribute(nb, "y") as number,
        }
        break
      }
    }
    spawn = spawn ?? focalPos ?? {
      x: next.getNodeAttribute(id, "x") as number,
      y: next.getNodeAttribute(id, "y") as number,
    }
    const jittered = { x: spawn.x + jitter(), y: spawn.y + jitter() }
    next.setNodeAttribute(id, "x", jittered.x)
    next.setNodeAttribute(id, "y", jittered.y)
    spawns.set(id, jittered)
  }
  return spawns
}

export interface MigrationPlan {
  enter: string[]
  exit: string[]
  targets: MorphTargets
}

/**
 * Build the morph plan AFTER layout has assigned final positions to `next`:
 * common nodes tween x/y/size, enters grow (size 0→final, spawnProgress
 * 0→1), exits shrink in place and are dropped on settle.
 */
export function planMigrationTargets(live: GraphLike, next: GraphLike): MigrationPlan {
  const enter: string[] = []
  const exit: string[] = []
  const targets: MorphTargets = new Map()

  for (const id of next.nodes()) {
    const x = next.getNodeAttribute(id, "x") as number
    const y = next.getNodeAttribute(id, "y") as number
    const size = next.getNodeAttribute(id, "size") as number
    if (live.hasNode(id)) {
      targets.set(id, { x, y, size })
    } else {
      enter.push(id)
      targets.set(id, { x, y, size, spawnProgress: 1 })
    }
  }
  for (const id of live.nodes()) {
    if (!next.hasNode(id)) {
      exit.push(id)
      targets.set(id, { size: EXIT_SIZE })
    }
  }
  return { enter, exit, targets }
}

/**
 * Snap non-positional styling/data attrs from the snapshot onto common
 * live nodes (community/trust/colors may change between fetches). Position
 * + size go through the morph; transient hover state stays untouched.
 */
export function syncCommonNodeAttrs(live: GraphLike, next: GraphLike, common: string[]): void {
  for (const id of common) {
    if (!next.hasNode(id) || !live.hasNode(id)) continue
    const snap = next.getNodeAttributes(id)
    for (const [key, value] of Object.entries(snap)) {
      if (NON_SYNC_ATTRS.has(key)) continue
      live.setNodeAttribute(id, key, value)
    }
  }
}

/**
 * Diff edges by their deterministic `src::tgt::type` keys: drop live-only
 * edges, add next-only edges (endpoints must already exist in live —
 * Atlas adds entering nodes first).
 */
export function syncEdges(live: GraphLike, next: GraphLike): void {
  for (const key of live.edges()) {
    if (!next.hasEdge(key)) live.dropEdge(key)
  }
  for (const key of next.edges()) {
    if (live.hasEdge(key)) continue
    const src = next.source(key)
    const tgt = next.target(key)
    if (!live.hasNode(src) || !live.hasNode(tgt)) continue
    live.addEdgeWithKey(key, src, tgt, { ...next.getEdgeAttributes(key) } as never)
  }
}
