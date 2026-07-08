// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Betweenness orchestrator (C1) — bridges a node/edge snapshot to
// graph-metrics.worker.ts and returns the score map. Surface-agnostic (takes
// raw ids + edge pairs, not a graphology Graph) so the map, Atlas, and 3D
// paths can all feed it. Callers cache the result per dataNodeKey — betweenness
// only changes when the node/edge set does.

export interface BetweennessOptions {
  /** Abort signal — terminates the worker mid-flight and rejects with AbortError. */
  signal?: AbortSignal
}

/**
 * Compute normalized betweenness centrality for the given graph off the main
 * thread. Resolves to `{ [nodeId]: score }` (graphology `normalized` scores).
 */
export function computeBetweenness(
  nodes: string[],
  edges: Array<[string, string]>,
  options: BetweennessOptions = {},
): Promise<Record<string, number>> {
  // Vite resolves this `new URL(...)` worker form at build time (same pattern
  // as apply-layout.ts). Gated inside the function so SSR/tests don't import
  // the worker bundle at module load.
  const worker = new Worker(
    new URL("../../workers/graph-metrics.worker.ts", import.meta.url),
    { type: "module" },
  )

  return new Promise<Record<string, number>>((resolve, reject) => {
    const cleanup = () => worker.terminate()

    if (options.signal) {
      const abortHandler = () => {
        cleanup()
        reject(new DOMException("Betweenness aborted", "AbortError"))
      }
      if (options.signal.aborted) {
        abortHandler()
        return
      }
      options.signal.addEventListener("abort", abortHandler, { once: true })
    }

    worker.onerror = (err) => {
      cleanup()
      reject(new Error(`Graph-metrics worker error: ${err.message}`))
    }

    worker.onmessage = (event) => {
      const msg = event.data as { type: string; payload: unknown }
      if (msg.type === "betweenness-done") {
        const scores = (msg.payload as { scores: Record<string, number> }).scores
        cleanup()
        resolve(scores)
      }
    }

    worker.postMessage({ type: "betweenness", payload: { nodes, edges } })
  })
}
