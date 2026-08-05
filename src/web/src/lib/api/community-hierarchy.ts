// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
import { mcpUrl, mcpHeaders, extractError } from "./common"

export interface CommunityHierarchyNode {
  community_id: string
  level: number
  parent_id: string | null
  member_count: number
  summary: string | null
  /**
   * c-TF-IDF keywords for the community (A3). Fallback label source when the
   * LLM summary is absent; joined into a "term · term · term" chip.
   */
  top_terms?: string[] | null
}

export interface CommunityHierarchy {
  levels: number
  nodes: CommunityHierarchyNode[]
}

export async function fetchCommunityHierarchy(signal?: AbortSignal): Promise<CommunityHierarchy> {
  const url = mcpUrl("/graph/community-hierarchy", {})
  const res = await fetch(url.toString(), { headers: mcpHeaders(), signal })
  if (!res.ok) {
    throw new Error(await extractError(res, `Community hierarchy fetch failed: ${res.status}`))
  }
  return res.json() as Promise<CommunityHierarchy>
}
