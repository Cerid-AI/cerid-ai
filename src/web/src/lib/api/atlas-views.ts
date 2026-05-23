// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Client for the /atlas/views API. Per-user named snapshots of an
// Atlas configuration (focal entity + hops + filter + active lenses
// + camera state).

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
  lenses?: string[]
  camera?: AtlasCameraState | null
}

export interface AtlasView extends AtlasViewInput {
  view_id: string
  created_at: string
  updated_at: string
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
  const url = opts?.mode
    ? `${BASE}?mode=${encodeURIComponent(opts.mode)}`
    : BASE
  const res = await fetch(url, { credentials: "include" })
  const payload = await asJson<{ views: AtlasView[] }>(res)
  return payload.views
}

export async function createAtlasView(input: AtlasViewInput): Promise<AtlasView> {
  const res = await fetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(input),
  })
  return asJson<AtlasView>(res)
}

export async function updateAtlasView(viewId: string, input: AtlasViewInput): Promise<AtlasView> {
  const res = await fetch(`${BASE}/${encodeURIComponent(viewId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(input),
  })
  return asJson<AtlasView>(res)
}

export async function deleteAtlasView(viewId: string): Promise<void> {
  const res = await fetch(`${BASE}/${encodeURIComponent(viewId)}`, {
    method: "DELETE",
    credentials: "include",
  })
  if (!res.ok && res.status !== 204) {
    throw new Error(`HTTP ${res.status}`)
  }
}
