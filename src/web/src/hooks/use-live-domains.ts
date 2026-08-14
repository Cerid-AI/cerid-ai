// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * UX-08 — one live source for "which domains can the user pick?".
 *
 * Per-surface hardcoded lists (Knowledge Console chips, New-Automation
 * picker) offered only the original taxonomy and silently omitted the
 * connector domains (mail / notes / messages) where all new connector
 * data lands. This hook derives the list from GET /graph/domains — the
 * same aggregate the Subjects tree renders — keeping every picker in
 * agreement with the graph.
 *
 * Only domains with artifacts are offered: retrieval and automations
 * filter over artifact domains, so an entity-only domain (e.g. a
 * derived "research" cluster) would be a chip that can never match.
 * Falls back to the static DOMAINS taxonomy while loading or when the
 * endpoint is unreachable — a picker with no options is worse than one
 * with the founding five.
 */

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { fetchDomainCounts } from "@/lib/api/domains"
import { DOMAINS } from "@/lib/types"

export function useLiveDomains(): string[] {
  // eslint-disable-next-line cerid/no-query-error-as-empty -- intentional fallback: an unreachable endpoint degrades to the static DOMAINS taxonomy below, never an empty picker
  const { data } = useQuery({
    // Same key wiki-pane / search-palette use for this aggregate — one
    // fetch feeds every domain-aware surface.
    queryKey: ["graph-domains"],
    queryFn: () => fetchDomainCounts(),
    staleTime: 10 * 60_000,
    retry: 1,
  })

  return useMemo(() => {
    const live = (data?.domains ?? [])
      .filter((d) => d.artifact_count > 0)
      .map((d) => d.name)
      .filter(Boolean)
    return live.length > 0 ? live : [...DOMAINS]
  }, [data])
}
