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
    // Light gravity (NOT strong) so the layout doesn't compress to the centre —
    // communities are free to push apart and read as distinct clusters.
    gravity: 0.5,
    scalingRatio: 12,
    strongGravityMode: false,
    barnesHutOptimize: nodeCount > BARNES_HUT_NODE_THRESHOLD,
    barnesHutTheta: 0.5,
    // Lower slowDown so nodes actually travel from the server seed into their
    // affinity clusters (8 was so damped the graph looked static); still high
    // enough to glide, not explode.
    slowDown: 4,
    adjustSizes: true,
    // linLog tightens intra-cluster spacing while repulsion separates clusters
    // — the clearest "communities pull together, push apart" structure.
    linLogMode: true,
    // Weight attraction by edge strength so strongly co-mentioned / similar
    // nodes pull together harder (visible affinity).
    edgeWeightInfluence: 1.5,
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
