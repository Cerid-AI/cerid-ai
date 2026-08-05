// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Analytics API client (Phase L).

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export interface IngestionDayBucket {
  date: string
  count: number
  domains: Record<string, number>
  intensity: number
}

export interface IngestionByDayResponse {
  window_days: number
  buckets: IngestionDayBucket[]
  total: number
  peak_count: number
}

export interface StageCost {
  stage: string
  cost_usd: number
  call_count: number
}

export interface SankeyEdge {
  source: string
  target: string
  value: number
}

export interface CostByStageResponse {
  window_days: number
  total_cost_usd: number
  stages: StageCost[]
  edges: SankeyEdge[]
}

export interface QualityTimelinePoint {
  date: string
  ndcg: number | null
  faithfulness: number | null
  memory_recall: number | null
  verification_accuracy: number | null
}

export interface QualityTimelineResponse {
  window_days: number
  points: QualityTimelinePoint[]
  latest: Record<string, number | null>
}

export async function fetchIngestionByDay(windowDays = 365): Promise<IngestionByDayResponse> {
  const res = await fetch(
    `${MCP_BASE}/analytics/ingestion-by-day?window_days=${windowDays}`,
    { headers: mcpHeaders() },
  )
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch ingestion heatmap"))
  return res.json()
}

export async function fetchCostByStage(windowDays = 30): Promise<CostByStageResponse> {
  const res = await fetch(
    `${MCP_BASE}/analytics/cost-by-stage?window_days=${windowDays}`,
    { headers: mcpHeaders() },
  )
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch cost breakdown"))
  return res.json()
}

export async function fetchQualityTimeline(windowDays = 90): Promise<QualityTimelineResponse> {
  const res = await fetch(
    `${MCP_BASE}/analytics/quality-timeline?window_days=${windowDays}`,
    { headers: mcpHeaders() },
  )
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch quality timeline"))
  return res.json()
}
