// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Community explorer API client (Phase R.2).
 *
 * Wraps the backend routes:
 *   GET /observability/communities?min_size=N&limit=N
 *   GET /observability/communities/{id}
 */

import { MCP_BASE, mcpHeaders } from "./common"
import type {
  CommunitySummary,
  CommunityFull,
  CommunityMemberEntity,
  RelatedCommunity,
} from "@/lib/types/community"

// ---------------------------------------------------------------------------
// Normalizers — adapt backend snake_case shapes to frontend types
// ---------------------------------------------------------------------------

function normalizeCommunityMember(raw: Record<string, unknown>): CommunityMemberEntity {
  return {
    canonical_id: String(raw.canonical_id ?? ""),
    name: String(raw.name ?? ""),
    entity_type: String(raw.entity_type ?? "OTHER"),
  }
}

function normalizeRelatedCommunity(raw: Record<string, unknown>): RelatedCommunity {
  return {
    community_id: String(raw.community_id ?? ""),
    co_mention_count: Number(raw.co_mention_count ?? 0),
  }
}

function normalizeCommunitySummary(raw: Record<string, unknown>): CommunitySummary {
  return {
    community_id: String(raw.community_id ?? ""),
    level: Number(raw.level ?? 0),
    summary: raw.summary != null ? String(raw.summary) : null,
    member_count: Number(raw.member_count ?? 0),
    last_summarized_at: raw.last_summarized_at != null ? String(raw.last_summarized_at) : null,
  }
}

function normalizeCommunityFull(raw: Record<string, unknown>): CommunityFull {
  const members = Array.isArray(raw.members)
    ? (raw.members as Record<string, unknown>[]).map(normalizeCommunityMember)
    : []
  const related = Array.isArray(raw.related_communities)
    ? (raw.related_communities as Record<string, unknown>[]).map(normalizeRelatedCommunity)
    : []

  return {
    ...normalizeCommunitySummary(raw),
    members,
    related_communities: related,
  }
}

// ---------------------------------------------------------------------------
// Public API functions
// ---------------------------------------------------------------------------

/**
 * GET /observability/communities?min_size=N&limit=N
 *
 * Returns communities ordered by member_count descending.
 * Only communities with cached LLM summaries are returned.
 */
export async function fetchCommunities({
  min_size = 3,
  limit = 30,
  level = 0,
}: {
  min_size?: number
  limit?: number
  level?: number
} = {}): Promise<CommunitySummary[]> {
  const params = new URLSearchParams({
    min_size: String(min_size),
    limit: String(limit),
    level: String(level),
  })
  const res = await fetch(`${MCP_BASE}/observability/communities?${params.toString()}`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    throw new Error(`Community list fetch failed (${res.status})`)
  }
  const rows = (await res.json()) as Record<string, unknown>[]
  return rows.map(normalizeCommunitySummary)
}

/**
 * GET /observability/communities/{id}
 *
 * Returns the full community page, or null on 404.
 */
export async function fetchCommunity(id: string): Promise<CommunityFull | null> {
  const res = await fetch(
    `${MCP_BASE}/observability/communities/${encodeURIComponent(id)}`,
    { headers: mcpHeaders() },
  )
  if (res.status === 404) return null
  if (!res.ok) {
    throw new Error(`Community detail fetch failed (${res.status})`)
  }
  const data = (await res.json()) as Record<string, unknown>
  return normalizeCommunityFull(data)
}
