// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * TanStack Query hooks for analytics panes (Tier A T4b).
 * Shared keys so invalidation from other surfaces stays consistent.
 */

import { useQuery } from "@tanstack/react-query"
import {
  fetchCostByStage,
  fetchIngestionByDay,
  fetchQualityTimeline,
  type CostByStageResponse,
  type IngestionByDayResponse,
  type QualityTimelineResponse,
} from "@/lib/api/analytics"
import { mcpUrl, mcpHeaders } from "@/lib/api/common"

export interface WikiFreshness {
  available: boolean
  total_entities?: number
  entities_with_summary?: number
  coverage_pct?: number
  active_entities?: number
  active_entities_with_summary?: number
  active_coverage_pct?: number
  unresolved_contradictions?: number
  log_activity_24h?: number
  reason?: string
}

interface HealthResponse {
  wiki_freshness?: WikiFreshness
}

export function useIngestionByDay(windowDays: number) {
  return useQuery<IngestionByDayResponse>({
    queryKey: ["analytics", "ingestion-by-day", windowDays],
    queryFn: () => fetchIngestionByDay(windowDays),
    staleTime: 60_000,
    // Operator Retry button is the recovery path — no silent multi-second retries.
    retry: false,
  })
}

export function useCostByStage(windowDays: number, enabled = true) {
  return useQuery<CostByStageResponse>({
    queryKey: ["analytics", "cost-by-stage", windowDays],
    queryFn: () => fetchCostByStage(windowDays),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

export function useQualityTimeline(windowDays: number, enabled = true) {
  return useQuery<QualityTimelineResponse>({
    queryKey: ["analytics", "quality-timeline", windowDays],
    queryFn: () => fetchQualityTimeline(windowDays),
    enabled,
    staleTime: 60_000,
    retry: false,
  })
}

export function useWikiFreshness() {
  return useQuery<WikiFreshness>({
    queryKey: ["analytics", "wiki-freshness"],
    queryFn: async () => {
      const r = await fetch(mcpUrl("/health").toString(), { headers: mcpHeaders() })
      if (!r.ok) throw new Error(`Health ${r.status}`)
      const body = (await r.json()) as HealthResponse
      if (!body.wiki_freshness) {
        throw new Error("Wiki freshness metrics not exposed yet")
      }
      return body.wiki_freshness
    },
    staleTime: 60_000,
    retry: false,
  })
}
