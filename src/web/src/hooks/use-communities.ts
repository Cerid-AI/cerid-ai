// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TanStack Query hooks for Leiden community explorer (Phase R.2).
 *
 * Communities are updated by the background `CommunityRefreshJob` cron.
 * 5-min staleTime + 60-second refetchInterval mirrors use-wiki-entities.ts.
 */

import { useQuery } from "@tanstack/react-query"
import { fetchCommunities, fetchCommunity } from "@/lib/api/community"
import type { CommunitySummary, CommunityFull } from "@/lib/types/community"

// ---------------------------------------------------------------------------
// Community list
// ---------------------------------------------------------------------------

export function useCommunities({
  min_size = 3,
  limit = 30,
  level = 0,
}: {
  min_size?: number
  limit?: number
  level?: number
} = {}): {
  data: CommunitySummary[] | undefined
  isLoading: boolean
  isError: boolean
} {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["communities", min_size, limit, level],
    queryFn: () => fetchCommunities({ min_size, limit, level }),
    staleTime: 5 * 60_000,
    refetchInterval: 60_000,
    retry: 1,
  })

  return { data, isLoading, isError }
}

// ---------------------------------------------------------------------------
// Single community detail
// ---------------------------------------------------------------------------

export function useCommunity(id: string | null): {
  data: CommunityFull | null | undefined
  isLoading: boolean
  isError: boolean
  isNotFound: boolean
} {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["community", id],
    queryFn: () => fetchCommunity(id!),
    enabled: !!id,
    staleTime: 5 * 60_000,
    refetchInterval: 60_000,
    retry: 1,
  })

  return {
    data,
    isLoading,
    isError,
    isNotFound: !isLoading && !isError && data === null,
  }
}
