// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// TanStack Query hook for the /graph/decomposition payload.

import { useQuery, keepPreviousData } from "@tanstack/react-query"
import {
  fetchDecomposition,
  fetchCommunityEntities,
  fetchBucketEntities,
} from "@/lib/api/decomposition"

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

/**
 * Entity leaves for a non-community bucket (UX-13). Pass a null key to
 * keep the query idle until the bucket row is expanded.
 */
export function useBucketEntities(
  key: { bucket: "unclustered" | "uncategorized"; domain: string | null } | null,
) {
  return useQuery({
    queryKey: ["graph-decomposition-bucket", key?.bucket, key?.domain],
    queryFn: ({ signal }) => fetchBucketEntities(key!.bucket, key!.domain, { signal }),
    staleTime: 60_000,
    enabled: Boolean(key),
    placeholderData: keepPreviousData,
  })
}
