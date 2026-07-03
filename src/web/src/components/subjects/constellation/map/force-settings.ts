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
    // Strong gravity anchors every node radially toward the centre in
    // proportion to its distance. Without it, the ~90%-degree-1 tail feels
    // almost no attraction and repulsion flings it to the rim — the corpus
    // collapses into a hollow "donut" and the disc-filled server seed is lost.
    // Strong mode keeps low-degree leaves in the body of the disc. Value tuned
    // against the live 3.3k-node corpus: inner-40%-radius fill 0.1% → ~20%,
    // outer-rim share 69% → ~12% (a filled disc with a natural edge taper).
    gravity: 8,
    // Low repulsion: enough to keep co-located nodes legible, low enough that it
    // never overpowers gravity into a ring (12 + linLog produced a hard donut).
    scalingRatio: 1,
    strongGravityMode: true,
    barnesHutOptimize: nodeCount > BARNES_HUT_NODE_THRESHOLD,
    barnesHutTheta: 0.5,
    // Damp travel from the server seed — the seed is already a filled disc, so
    // the sim should settle it into an organic float, not re-derive the layout.
    slowDown: 6,
    adjustSizes: true,
    // linLog off: for a hub-and-leaf topology it pushes the degree-1 tail into
    // an outer ring. Plain FA2 attraction keeps the disc filled; scalingRatio
    // still gives clusters their separation.
    linLogMode: false,
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
