// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Client for the /atlas/views API. Per-user named snapshots of an
// Atlas configuration (focal entity + hops + filter + active lenses
// + chips + camera state).
//
// Version field (amendment 5): payload version 2 adds focal, hops,
// lens[], chips[], camera. Unversioned (v0/v1) payloads are tolerated
// by the loader — unknown fields are ignored gracefully.
//
// Cycle 4 v3: adds layout, viewDim, camera3d, pinnedNodes, atlasTier.
// All v3 fields are optional — v0–v2 views load unchanged.

import { mcpUrl, mcpHeaders } from "./common"
import type { LensId } from "@/lib/graph/lenses"
import {
  ATLAS_VIEW_VERSION_V3,
  type MapLayout,
  type AtlasTierPosition,
} from "@/lib/graph/cycle4-contracts"

export const ATLAS_VIEW_VERSION = 2 as const
export { ATLAS_VIEW_VERSION_V3 }

export interface AtlasCameraState {
  x: number
  y: number
  ratio: number
  angle: number
}

export interface AtlasViewInput {
  name: string
  entity: string
  hops: number
  filter?: string | null
  mode?: string
  /** Payload version (2 = Meridian, 3 = Cycle 4 STRATA). Unversioned payloads treated as v0. */
  version?: number
  lenses?: string[]
  /** Active entity-type chip filters */
  chips?: string[]
  camera?: AtlasCameraState | null
  // v3 fields (all optional — v0–v2 views load unchanged via normalizeView)
  /** Cycle 4 layout base ("force" | "wells" | "domain"). */
  layout?: MapLayout
  /** Constellation sub-mode: "map" = 2D sigma, "3d" = R3F scene. */
  viewDim?: "map" | "3d"
  /** R3F camera state — sibling of AtlasCameraState (which stays sigma-shaped). */
  camera3d?: {
    position: [number, number, number]
    target: [number, number, number]
  }
  /** Pinned node overrides: entity id → { x, y } in map coordinates. */
  pinnedNodes?: Record<string, { x: number; y: number }>
  /**
   * A2: decomposition ladder position saved by Agent B.
   * Agent C types/normalizes; restore walks the path like a palette pick.
   */
  atlasTier?: AtlasTierPosition
}

export interface AtlasView extends AtlasViewInput {
  view_id: string
  created_at: string
  updated_at: string
}

/**
 * Tolerant loader: normalizes a raw view (possibly from an older payload
 * that lacks version/chips) into a full AtlasView with safe defaults.
 * v3 optional fields are passed through unchanged; absent fields remain
 * undefined so callers can distinguish "not saved" from "saved as null".
 */
export function normalizeView(raw: AtlasView): AtlasView {
  return {
    ...raw,
    version: raw.version ?? 0,
    lenses: Array.isArray(raw.lenses) ? raw.lenses : [],
    chips: Array.isArray(raw.chips) ? raw.chips : [],
    camera: raw.camera ?? null,
    // v3 fields: pass through if present, omit if absent (tolerant)
    layout: raw.layout,
    viewDim: raw.viewDim,
    camera3d: raw.camera3d,
    pinnedNodes: raw.pinnedNodes,
    atlasTier: raw.atlasTier,
  }
}

/**
 * Build a v2 AtlasViewInput ready to save (legacy — Atlas neighborhood views).
 */
export function buildViewPayload(opts: {
  name: string
  entity: string
  hops: number
  filter?: string | null
  activeLenses: Set<LensId>
  activeChips: Set<string>
  camera: AtlasCameraState | null
}): AtlasViewInput {
  return {
    name: opts.name,
    entity: opts.entity,
    hops: opts.hops,
    filter: opts.filter ?? null,
    mode: "atlas",
    version: ATLAS_VIEW_VERSION,
    lenses: Array.from(opts.activeLenses),
    chips: Array.from(opts.activeChips),
    camera: opts.camera,
  }
}

const BASE = "/atlas/views"

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error((detail as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export async function listAtlasViews(opts?: { mode?: string }): Promise<AtlasView[]> {
  const url = mcpUrl(BASE, { mode: opts?.mode })
  const res = await fetch(url.toString(), { headers: mcpHeaders() })
  const payload = await asJson<{ views: AtlasView[] }>(res)
  return payload.views.map(normalizeView)
}

export async function createAtlasView(input: AtlasViewInput): Promise<AtlasView> {
  const res = await fetch(mcpUrl(BASE).toString(), {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  })
  return normalizeView(await asJson<AtlasView>(res))
}

export async function deleteAtlasView(viewId: string): Promise<void> {
  const res = await fetch(mcpUrl(`${BASE}/${encodeURIComponent(viewId)}`).toString(), {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok && res.status !== 204) {
    throw new Error(`HTTP ${res.status}`)
  }
}
