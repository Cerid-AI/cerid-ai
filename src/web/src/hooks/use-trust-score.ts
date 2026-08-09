// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useQuery } from "@tanstack/react-query"
import { fetchTrustScore } from "@/lib/api/trust-score"
import type { TrustScore } from "@/lib/types/trust-score"

/**
 * Fetch and cache the system TrustScore.
 *
 * The score is computed nightly, so a 5-minute stale time and 60-second
 * refetch interval are generous enough to avoid hammering the backend
 * while staying fresh across a long session.
 *
 * On error the hook returns `{ data: undefined, isError: true }` — callers
 * should render nothing (operator concern, not user-visible).
 */
export function useTrustScore(): {
  data: TrustScore | undefined
  isLoading: boolean
  isError: boolean
  error: Error | null
  refetch: () => void
} {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["trust-score"],
    queryFn: fetchTrustScore,
    refetchInterval: 60_000,
    staleTime: 5 * 60_000,
    retry: 1,
  })

  return {
    data,
    isLoading,
    isError,
    error: error as Error | null,
    refetch: () => { void refetch() },
  }
}
