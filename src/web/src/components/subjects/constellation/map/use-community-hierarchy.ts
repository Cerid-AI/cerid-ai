// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
import { useQuery } from "@tanstack/react-query"
import { fetchCommunityHierarchy } from "@/lib/api/community-hierarchy"

export function useCommunityHierarchy(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["community-hierarchy"],
    queryFn: ({ signal }) => fetchCommunityHierarchy(signal),
    staleTime: 5 * 60 * 1000,
    enabled: options.enabled ?? true,
  })
}
