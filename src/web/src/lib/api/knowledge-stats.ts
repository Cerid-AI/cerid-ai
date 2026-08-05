// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { MCP_BASE, mcpHeaders, extractError } from "./common"

/**
 * Knowledge Stats — corpus-growth payload shape. Single read returns
 * the 5 orthogonal dimensions (nodes / edges / chunks / diversity /
 * growth) for the F9 hero card. Backed by /observability/knowledge-stats.
 */
export interface KnowledgeStatsNodes {
  artifacts: number
  entities: number
  memories: number
  sources: number
}

export interface KnowledgeStatsEdges {
  mentions: number
  relates_to: number
  wikilinks: number
  from_source: number
  has_contradiction: number
}

export interface KnowledgeStatsDiversity {
  source_kinds: number
  domains: number
}

export interface KnowledgeStatsGrowth {
  artifacts_24h: number
  artifacts_7d: number
  first_artifact_at: string | null
  corpus_age_days: number
}

export interface KnowledgeStats {
  nodes: KnowledgeStatsNodes
  edges: KnowledgeStatsEdges
  chunks: number
  diversity: KnowledgeStatsDiversity
  growth: KnowledgeStatsGrowth
  captured_at: string
}

export interface KnowledgeStatsHistorySnapshot extends KnowledgeStats {
  date: string
}

export interface KnowledgeStatsHistoryResponse {
  days: number
  snapshots: KnowledgeStatsHistorySnapshot[]
}

/**
 * Fetch the current corpus snapshot. Server caches in Redis for 60s
 * so a busy Sources pane doesn't hammer Neo4j.
 */
export async function fetchKnowledgeStats(): Promise<KnowledgeStats> {
  const res = await fetch(`${MCP_BASE}/observability/knowledge-stats`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    throw new Error(await extractError(res, "fetchKnowledgeStats"))
  }
  return (await res.json()) as KnowledgeStats
}

/**
 * Fetch daily snapshots for sparkline rendering. ``days`` clamped to
 * 1–365 server-side.
 */
export async function fetchKnowledgeStatsHistory(
  days: number = 30,
): Promise<KnowledgeStatsHistoryResponse> {
  const res = await fetch(
    `${MCP_BASE}/observability/knowledge-stats/history?days=${days}`,
    { headers: mcpHeaders() },
  )
  if (!res.ok) {
    throw new Error(await extractError(res, "fetchKnowledgeStatsHistory"))
  }
  return (await res.json()) as KnowledgeStatsHistoryResponse
}
