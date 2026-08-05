// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Client for /graph/embeddings/3d — UMAP-projected 3D coordinates for
// every entity in scope. Used by Constellation mode (Phase B).

import { mcpUrl, mcpHeaders, extractError } from "./common"

/**
 * Ceiling for graph payload fetches. Without it a slow backend leaves the
 * request hanging until TanStack supersedes it (nginx 499) and the view
 * shows a spinner forever instead of the error card + Retry.
 */
export const GRAPH_FETCH_TIMEOUT_MS = 30_000

/**
 * Compose the caller's (React Query) signal with a hard timeout. Prefers
 * native AbortSignal.any/timeout; falls back to manual composition where
 * either is missing (older WebKit/jsdom).
 */
export function withRequestTimeout(signal?: AbortSignal, timeoutMs = GRAPH_FETCH_TIMEOUT_MS): AbortSignal {
  if (typeof AbortSignal.any === "function" && typeof AbortSignal.timeout === "function") {
    const timeout = AbortSignal.timeout(timeoutMs)
    return signal ? AbortSignal.any([signal, timeout]) : timeout
  }
  return composeTimeoutSignal(signal, timeoutMs)
}

/** Manual fallback for withRequestTimeout — exported for direct unit testing. */
export function composeTimeoutSignal(signal: AbortSignal | undefined, timeoutMs: number): AbortSignal {
  const controller = new AbortController()
  const timer = setTimeout(() => {
    controller.abort(new DOMException(`Request timed out after ${timeoutMs}ms`, "TimeoutError"))
  }, timeoutMs)
  if (signal) {
    if (signal.aborted) {
      clearTimeout(timer)
      controller.abort(signal.reason)
    } else {
      signal.addEventListener(
        "abort",
        () => {
          clearTimeout(timer)
          controller.abort(signal.reason)
        },
        { once: true },
      )
    }
  }
  return controller.signal
}

/** Map an abort caused by the timeout signal to an actionable error. */
export function timeoutToError(err: unknown, what: string): unknown {
  if (err instanceof DOMException && err.name === "TimeoutError") {
    return new Error(`${what} timed out — the backend may still be computing. Retry in a moment.`)
  }
  return err
}

export interface EntityEmbedding3D {
  /** Canonical entity id */
  id: string
  name: string
  /** UMAP x coordinate (or PCA fallback) */
  x: number
  y: number
  z: number
  /** Entity type — drives color cluster */
  type: string
  /** Leiden community id — drives bloom color */
  community: string | null
  /** Mention count for node size */
  mention_count: number
  /** verified / partial / unverified / contradicted / unknown */
  trust_state: string
  /** Projection algorithm used: "umap" or "pca" (fallback) */
  projection: "umap" | "pca"
  /** Primary domain derived by DeriveDomainsJob; null until first derivation run */
  primary_domain?: string | null
  /** Entity birth timestamp (ISO); drives the timebar filter + timelapse (A8/A9). */
  created_at?: string | null
}

export interface Embeddings3DResponse {
  /** Total entities in payload */
  count: number
  entities: EntityEmbedding3D[]
  /**
   * CO_MENTIONED and SIMILAR_TO linkage as [sourceIdx, targetIdx, weight, kind]
   * 4-tuples indexing into `entities`. kind is "co_mention" or "similar".
   * Drives the neural-net edge layer.
   */
  links: [number, number, number, string][]
  /** Whether served from cache */
  cached: boolean
  /** ISO timestamp the projection was last computed */
  computed_at: string | null
  /** Number of isolated (degree-0) entities excluded from the graph when include_isolated=false */
  isolated_count: number
}

export interface FetchEmbeddings3DOptions {
  /** Restrict to a comma-separated subset of entity ids */
  entities?: string[]
  /** Restrict by entity type */
  filter?: string | null
  /** When true, include isolated (degree-0) entities in the response */
  includeIsolated?: boolean
  signal?: AbortSignal
}

export async function fetchEmbeddings3D(
  options: FetchEmbeddings3DOptions = {},
): Promise<Embeddings3DResponse> {
  const url = mcpUrl("/graph/embeddings/3d", {
    entities: options.entities && options.entities.length > 0 ? options.entities.join(",") : undefined,
    filter: options.filter ?? undefined,
    // Omit the param when false to keep default URLs cache-stable.
    ...(options.includeIsolated ? { include_isolated: "true" } : {}),
  })
  let res: Response
  try {
    res = await fetch(url.toString(), { headers: mcpHeaders(), signal: withRequestTimeout(options.signal) })
  } catch (err) {
    throw timeoutToError(err, "3D embeddings fetch")
  }
  if (!res.ok) throw new Error(await extractError(res, `3D embeddings fetch failed: ${res.status}`))
  return res.json() as Promise<Embeddings3DResponse>
}
