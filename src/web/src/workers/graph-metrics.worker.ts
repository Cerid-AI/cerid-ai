// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Graph-metrics Web Worker (C1) — betweenness centrality off the main thread.
// Brandes' algorithm is O(V*E); on the full corpus (~3K nodes / ~18K edges)
// that's a couple of seconds, so it runs here to keep the renderer at 60fps.
// The main thread caches the result per dataNodeKey (see compute-betweenness.ts).
//
// Message protocol:
//   in:  {type: "betweenness", payload: {nodes: string[], edges: [src,tgt][]}}
//   out: {type: "betweenness-done", payload: {scores: Record<id, number>}}
//
// Scores are graphology's `normalized` betweenness (0..1 against the theoretical
// max); the ramp in bridges.ts re-normalizes against the observed max.

import Graph from "graphology"
import betweennessCentrality from "graphology-metrics/centrality/betweenness"

interface MetricsRequest {
  type: "betweenness"
  payload: { nodes: string[]; edges: Array<[string, string]> }
}

interface MetricsDone {
  type: "betweenness-done"
  payload: { scores: Record<string, number> }
}

function runBetweenness(payload: MetricsRequest["payload"]): void {
  const graph = new Graph({ type: "undirected", multi: false, allowSelfLoops: false })
  for (const id of payload.nodes) {
    if (!graph.hasNode(id)) graph.addNode(id)
  }
  for (const [s, t] of payload.edges) {
    if (s === t) continue
    if (!graph.hasNode(s) || !graph.hasNode(t)) continue
    if (graph.hasEdge(s, t)) continue
    graph.addEdge(s, t)
  }
  const scores = betweennessCentrality(graph, { normalized: true })
  const result: MetricsDone = { type: "betweenness-done", payload: { scores } }
  ;(self as unknown as Worker).postMessage(result)
}

self.onmessage = (event: MessageEvent<MetricsRequest>): void => {
  const msg = event.data
  if (msg.type === "betweenness") {
    // Exceptions propagate to the parent via the worker's onerror — no
    // useless try/catch here (mirrors atlas-layout.worker.ts).
    runBetweenness(msg.payload)
  }
}

export type { MetricsRequest, MetricsDone }
