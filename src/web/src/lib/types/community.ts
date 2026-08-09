// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * TypeScript value objects for the Leiden community explorer (Phase R.2).
 *
 * Mirror the Pydantic models in app.db.neo4j.communities:
 *   CommunitySummary → CommunitySummary
 *   CommunityFull    → CommunityFull
 */

/** Lightweight summary row returned by GET /observability/communities */
export interface CommunitySummary {
  community_id: string
  level: number
  summary: string | null
  member_count: number
  last_summarized_at: string | null
}

/** Minimal entity reference inside a community detail */
export interface CommunityMemberEntity {
  canonical_id: string
  name: string
  entity_type: string
}

/** Another community that frequently co-occurs with this one */
export interface RelatedCommunity {
  community_id: string
  co_mention_count: number
}

/** Full community detail returned by GET /observability/communities/{id} */
export interface CommunityFull extends CommunitySummary {
  members: CommunityMemberEntity[]
  related_communities: RelatedCommunity[]
}
