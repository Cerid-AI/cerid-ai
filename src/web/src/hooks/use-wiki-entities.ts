// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TanStack Query hooks for entity wiki pages (Phase W.1).
 *
 * Wiki updates are background-job-driven, not live — 5-min staleTime and
 * 60-second refetch keep the data fresh across a long session without
 * hammering the backend.
 */

import { useQuery, type QueryObserverResult } from "@tanstack/react-query"
import { fetchWikiEntities, fetchWikiEntity } from "@/lib/api/wiki"
import type { EntitySummary, WikiEntityPage } from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Entity list
// ---------------------------------------------------------------------------

export function useWikiEntities({
  limit = 30,
  q,
  includeInternal = false,
}: { limit?: number; q?: string; includeInternal?: boolean } = {}): {
  data: EntitySummary[] | undefined
  isLoading: boolean
  isError: boolean
  refetch: () => Promise<QueryObserverResult<EntitySummary[], Error>>
} {
  const search = q?.trim() || undefined
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["wiki-entities", limit, search ?? null, includeInternal],
    queryFn: () => fetchWikiEntities({ limit, q: search, includeInternal }),
    staleTime: 5 * 60_000,
    refetchInterval: 60_000,
    retry: 1,
  })

  return { data, isLoading, isError, refetch }
}

// ---------------------------------------------------------------------------
// Single entity page
// ---------------------------------------------------------------------------

export function useWikiEntity(slug: string | null): {
  data: WikiEntityPage | null | undefined
  isLoading: boolean
  isError: boolean
  isNotFound: boolean
  refetch: () => Promise<QueryObserverResult<WikiEntityPage | null, Error>>
} {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["wiki-entity", slug],
    queryFn: () => fetchWikiEntity(slug!),
    enabled: !!slug,
    staleTime: 5 * 60_000,
    refetchInterval: 60_000,
    retry: 1,
  })

  return {
    data,
    isLoading,
    isError,
    isNotFound: !isLoading && !isError && data === null,
    refetch,
  }
}
