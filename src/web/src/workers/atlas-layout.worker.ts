// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Atlas layout Web Worker — runs force-atlas2 layout off the main thread
// so the renderer stays at 60fps during large graph computations.
//
// Message protocol:
//   in: {type: "layout", payload: {nodes: [{id, mention_count}], edges: [{source,target,weight}], iterations: 100}}
//   out: {type: "layout-done", payload: {positions: Record<id, {x, y}>}}
//   out: {type: "layout-progress", payload: {iteration: N, total: M}}
//
// The main thread sends raw node/edge data (NOT a graphology Graph — that's
// not transferable across the worker boundary cheanly), receives back a
// Map<id, {x, y}> that the renderer merges into its in-memory graphology
// instance.

import Graph from "graphology"
import forceAtlas2 from "graphology-layout-forceatlas2"

interface LayoutNode {
  id: string
  mention_count: number
}
interface LayoutEdge {
  source: string
  target: string
  weight: number
}

interface LayoutRequest {
  type: "layout"
  payload: {
    nodes: LayoutNode[]
    edges: LayoutEdge[]
    iterations: number
  }
}

interface LayoutProgress {
  type: "layout-progress"
  payload: { iteration: number; total: number }
}

interface LayoutDone {
  type: "layout-done"
  payload: { positions: Record<string, { x: number; y: number }> }
}

type WorkerOut = LayoutProgress | LayoutDone

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

const PROGRESS_INTERVAL = 25  // emit every N iterations

function runLayout(payload: LayoutRequest["payload"]): void {
  // Build a minimal graphology instance just for layout. We use random
  // initial positions because force-atlas2 needs starting coords; the
  // algorithm converges from there.
  const graph = new Graph({ multi: false, allowSelfLoops: false })
  for (const node of payload.nodes) {
    graph.addNode(node.id, {
      x: Math.random(),
      y: Math.random(),
      // forceatlas2 reads "size" if present; map from mention_count
      size: 1 + Math.log1p(node.mention_count),
    })
  }
  for (const edge of payload.edges) {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue
    if (graph.hasEdge(edge.source, edge.target)) continue
    if (edge.source === edge.target) continue
    graph.addEdge(edge.source, edge.target, { weight: edge.weight })
  }

  // forceAtlas2 takes iterations + settings. We use the "barnesHut" optimisation
  // for >500 nodes (kicks in automatically via library defaults when scalingRatio
  // is reasonable). Settings tuned per the validation research's recommendations.
  const settings = {
    gravity: 1,
    scalingRatio: 10,
    strongGravityMode: true,
    barnesHutOptimize: graph.order > 500,
    barnesHutTheta: 0.5,
    slowDown: 1,
    adjustSizes: true,
    linLogMode: false,
    outboundAttractionDistribution: false,
    edgeWeightInfluence: 1,
  }

  // Iterate manually so we can emit progress events at intervals
  const totalIterations = Math.max(1, payload.iterations)
  const batchSize = Math.max(1, Math.floor(totalIterations / 20))
  let done = 0
  while (done < totalIterations) {
    const thisBatch = Math.min(batchSize, totalIterations - done)
    forceAtlas2.assign(graph, { iterations: thisBatch, settings })
    done += thisBatch
    if (done % PROGRESS_INTERVAL === 0 || done === totalIterations) {
      const progress: LayoutProgress = {
        type: "layout-progress",
        payload: { iteration: done, total: totalIterations },
      }
      ;(self as unknown as Worker).postMessage(progress)
    }
  }

  // Extract positions
  const positions: Record<string, { x: number; y: number }> = {}
  graph.forEachNode((id, attrs) => {
    positions[id] = { x: attrs.x as number, y: attrs.y as number }
  })

  const result: LayoutDone = {
    type: "layout-done",
    payload: { positions },
  }
  ;(self as unknown as Worker).postMessage(result)
}

// ---------------------------------------------------------------------------
// Worker message handler
// ---------------------------------------------------------------------------

self.onmessage = (event: MessageEvent<LayoutRequest>): void => {
  const msg = event.data
  if (msg.type === "layout") {
    // runLayout exceptions propagate to the parent thread via the worker's
    // built-in error handling (onerror). No try/catch needed here —
    // catching and re-throwing is a no-op (ESLint no-useless-catch).
    runLayout(msg.payload)
  }
}

// Export types so consumers can import them without importing the worker bundle
export type { LayoutRequest, LayoutProgress, LayoutDone, WorkerOut }
