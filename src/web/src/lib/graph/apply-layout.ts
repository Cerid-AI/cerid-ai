// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Layout orchestrator — bridges a graphology Graph to the
// atlas-layout.worker.ts and merges resulting positions back into the
// graph's node attributes.
//
// Runs the layout off the main thread so the Atlas renderer stays 60fps
// during compute. The worker is constructed via Vite's `?worker` import
// suffix which produces a proper Web Worker bundle.

import type Graph from "graphology"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"

// Worker is intentionally constructed inside applyLayout so each call
// gets a fresh isolate. Vite's `?worker` suffix produces a Worker class.
// Importing from a relative path so the bundler resolves correctly.

export interface LayoutOptions {
  /** Force-atlas2 iterations. Default 250 (good convergence at < 2K nodes). */
  iterations?: number
  /**
   * Optional progress callback for UX (status bar / progress chip).
   * Fires every ~25 iterations.
   */
  onProgress?: (iteration: number, total: number) => void
  /**
   * Optional abort signal — if triggered, the worker is terminated mid-flight
   * and the promise rejects with an AbortError.
   */
  signal?: AbortSignal
  /**
   * Warm-start (A5 ego migration): ship each node's current x/y to the
   * worker as its FA2 starting position instead of random init, so the
   * layout relaxes from the existing mental map. Callers must seed
   * positions first (all-zero coords would stack every node).
   */
  warmStart?: boolean
}

export interface LayoutResult {
  /** Wall-clock duration of the layout pass (ms). For perf tracking. */
  durationMs: number
  /** Number of nodes the worker processed. */
  nodeCount: number
  /** Number of force-atlas2 iterations actually executed. */
  iterations: number
}

/**
 * Compute force-atlas2 layout for the given graphology graph by offloading
 * to the atlas-layout worker. Mutates the graph's nodes in place — each
 * node's `x` and `y` attributes are updated with the layout result.
 *
 * The caller is responsible for triggering sigma.refresh() after the
 * promise resolves so the renderer picks up new positions.
 */
export async function applyLayout(
  graph: Graph<AtlasNodeAttributes, AtlasEdgeAttributes>,
  options: LayoutOptions = {},
): Promise<LayoutResult> {
  const iterations = options.iterations ?? 250
  const start = performance.now()

  // Snapshot the data the worker needs — graphology Graph instances aren't
  // structured-cloneable across the worker boundary.
  const nodes = graph.mapNodes((id, attrs) => ({
    id,
    mention_count: attrs.mention_count,
    ...(options.warmStart ? { x: attrs.x, y: attrs.y } : {}),
  }))
  const edges = graph.mapEdges((_key, attrs) => ({
    source: attrs.source,
    target: attrs.target,
    weight: attrs.weight,
  }))

  // Vite's `?worker` import produces a Worker class; importing here is
  // gated behind the function so SSR / tests don't blow up on import.
  // The path uses `new URL(...)` form which Vite resolves at build time.
  const worker = new Worker(
    new URL("../../workers/atlas-layout.worker.ts", import.meta.url),
    { type: "module" },
  )

  return new Promise<LayoutResult>((resolve, reject) => {
    const cleanup = () => {
      worker.terminate()
    }

    if (options.signal) {
      const abortHandler = () => {
        cleanup()
        reject(new DOMException("Layout aborted", "AbortError"))
      }
      if (options.signal.aborted) {
        abortHandler()
        return
      }
      options.signal.addEventListener("abort", abortHandler, { once: true })
    }

    worker.onerror = (err) => {
      cleanup()
      reject(new Error(`Atlas layout worker error: ${err.message}`))
    }

    worker.onmessage = (event) => {
      const msg = event.data as { type: string; payload: unknown }
      if (msg.type === "layout-progress") {
        const { iteration, total } = msg.payload as { iteration: number; total: number }
        options.onProgress?.(iteration, total)
        return
      }
      if (msg.type === "layout-done") {
        const positions = (msg.payload as {
          positions: Record<string, { x: number; y: number }>
        }).positions
        for (const [id, { x, y }] of Object.entries(positions)) {
          if (graph.hasNode(id)) {
            graph.setNodeAttribute(id, "x", x)
            graph.setNodeAttribute(id, "y", y)
          }
        }
        cleanup()
        resolve({
          durationMs: performance.now() - start,
          nodeCount: nodes.length,
          iterations,
        })
      }
    }

    worker.postMessage({
      type: "layout",
      payload: { nodes, edges, iterations },
    })
  })
}
