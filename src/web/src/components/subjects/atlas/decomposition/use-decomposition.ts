// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// TanStack Query hook for the /graph/decomposition payload.

import { useQuery, keepPreviousData } from "@tanstack/react-query"
import { fetchDecomposition, fetchCommunityEntities } from "@/lib/api/decomposition"

export function useDecomposition() {
  return useQuery({
    queryKey: ["graph-decomposition"],
    queryFn: ({ signal }) => fetchDecomposition({ signal }),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  })
}

export function useCommunityEntities(communityId: string | null) {
  return useQuery({
    queryKey: ["graph-decomposition-community", communityId],
    queryFn: ({ signal }) => fetchCommunityEntities(communityId!, { signal }),
    staleTime: 60_000,
    enabled: Boolean(communityId),
    placeholderData: keepPreviousData,
  })
}
