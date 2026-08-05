// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Client for POST /graph/tour/generate — Constellation tour mode.
// Pro-gated; the endpoint returns 403 for community-tier users.

export interface TourStop {
  entity_id: string
  entity_name: string
  camera: [number, number, number]
  look_at: [number, number, number]
  duration_ms: number
  narration: string
}

export interface TourArc {
  stops: TourStop[]
  total_duration_ms: number
  summary: string
}

export interface GenerateTourBody {
  focal_entity?: string | null
  max_stops?: number
  duration_s?: number
}

import { mcpUrl, mcpHeaders } from "./common"

const BASE = "/graph/tour"

export async function generateTour(body: GenerateTourBody = {}): Promise<TourArc> {
  const res = await fetch(mcpUrl(`${BASE}/generate`).toString(), {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error((detail as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<TourArc>
}

export interface TourHealth {
  pro_visualization_tour_enabled: boolean
  max_stops: number
  default_duration_s: number
}

export async function fetchTourHealth(): Promise<TourHealth> {
  const res = await fetch(mcpUrl(`${BASE}/health`).toString(), { headers: mcpHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<TourHealth>
}
