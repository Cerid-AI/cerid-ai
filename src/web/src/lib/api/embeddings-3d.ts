// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Client for /graph/embeddings/3d — UMAP-projected 3D coordinates for
// every entity in scope. Used by Constellation mode (Phase B).

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

const BASE = "/graph/embeddings/3d"

export async function fetchEmbeddings3D(
  options: FetchEmbeddings3DOptions = {},
): Promise<Embeddings3DResponse> {
  const params = new URLSearchParams()
  if (options.entities && options.entities.length > 0) {
    params.set("entities", options.entities.join(","))
  }
  if (options.filter) params.set("filter", options.filter)
  const url = params.toString() ? `${BASE}?${params}` : BASE
  const res = await fetch(url, { credentials: "include", signal: options.signal })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error((detail as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<Embeddings3DResponse>
}
