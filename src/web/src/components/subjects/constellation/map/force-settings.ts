// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure ForceAtlas2 settings + gating for the live 2D map layout. Tuned per
// the Obsidian-class spec: Barnes-Hut for large graphs, strong gravity so
// components stay anchored near the server seed (mental-map stability), and a
// non-trivial slowDown to damp jitter into an organic float.

export interface FA2Settings {
  gravity: number
  scalingRatio: number
  strongGravityMode: boolean
  barnesHutOptimize: boolean
  barnesHutTheta: number
  slowDown: number
  adjustSizes: boolean
  linLogMode: boolean
  edgeWeightInfluence: number
}

const BARNES_HUT_NODE_THRESHOLD = 500

export function buildForceSettings(nodeCount: number): FA2Settings {
  return {
    gravity: 1,
    scalingRatio: 10,
    strongGravityMode: true,
    barnesHutOptimize: nodeCount > BARNES_HUT_NODE_THRESHOLD,
    barnesHutTheta: 0.5,
    // Higher slowDown damps the per-tick step so the graph glides from the
    // seed instead of exploding, then breathes at low energy.
    slowDown: 8,
    adjustSizes: true,
    linLogMode: false,
    edgeWeightInfluence: 1,
  }
}

export function shouldRunLayout(opts: {
  reducedMotion: boolean
  liveLayout: boolean
  nodeCount: number
}): boolean {
  if (opts.reducedMotion) return false
  if (!opts.liveLayout) return false
  return opts.nodeCount > 0
}
