// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Client for the /atlas/views API. Per-user named snapshots of an
// Atlas configuration (focal entity + hops + filter + active lenses
// + chips + camera state).
//
// Version field (amendment 5): payload version 2 adds focal, hops,
// lens[], chips[], camera. Unversioned (v0/v1) payloads are tolerated
// by the loader — unknown fields are ignored gracefully.

import { mcpUrl, mcpHeaders } from "./common"
import type { LensId } from "@/lib/graph/lenses"

export const ATLAS_VIEW_VERSION = 2 as const

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
  /** Payload version (2 = Meridian). Unversioned payloads treated as v0. */
  version?: number
  lenses?: string[]
  /** Active entity-type chip filters */
  chips?: string[]
  camera?: AtlasCameraState | null
}

export interface AtlasView extends AtlasViewInput {
  view_id: string
  created_at: string
  updated_at: string
}

/**
 * Tolerant loader: normalizes a raw view (possibly from an older payload
 * that lacks version/chips) into a full AtlasView with safe defaults.
 */
export function normalizeView(raw: AtlasView): AtlasView {
  return {
    ...raw,
    version: raw.version ?? 0,
    lenses: Array.isArray(raw.lenses) ? raw.lenses : [],
    chips: Array.isArray(raw.chips) ? raw.chips : [],
    camera: raw.camera ?? null,
  }
}

/**
 * Build a v2 AtlasViewInput ready to save.
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

export async function updateAtlasView(viewId: string, input: AtlasViewInput): Promise<AtlasView> {
  const res = await fetch(mcpUrl(`${BASE}/${encodeURIComponent(viewId)}`).toString(), {
    method: "PATCH",
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
