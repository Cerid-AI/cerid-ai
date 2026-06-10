// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Client for /graph/embeddings/3d — UMAP-projected 3D coordinates for
// every entity in scope. Used by Constellation mode (Phase B).

import { mcpUrl, mcpHeaders, extractError } from "./common"

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
}

export interface Embeddings3DResponse {
  /** Total entities in payload */
  count: number
  entities: EntityEmbedding3D[]
  /**
   * CO_MENTIONED linkage as [sourceIdx, targetIdx, weight] triples
   * indexing into `entities`. Drives the neural-net edge layer.
   */
  links: [number, number, number][]
  /** Whether served from cache */
  cached: boolean
  /** ISO timestamp the projection was last computed */
  computed_at: string | null
}

export interface FetchEmbeddings3DOptions {
  /** Restrict to a comma-separated subset of entity ids */
  entities?: string[]
  /** Restrict by entity type */
  filter?: string | null
  signal?: AbortSignal
}

export async function fetchEmbeddings3D(
  options: FetchEmbeddings3DOptions = {},
): Promise<Embeddings3DResponse> {
  const url = mcpUrl("/graph/embeddings/3d", {
    entities: options.entities && options.entities.length > 0 ? options.entities.join(",") : undefined,
    filter: options.filter ?? undefined,
  })
  const res = await fetch(url.toString(), { headers: mcpHeaders(), signal: options.signal })
  if (!res.ok) throw new Error(await extractError(res, `3D embeddings fetch failed: ${res.status}`))
  return res.json() as Promise<Embeddings3DResponse>
}
