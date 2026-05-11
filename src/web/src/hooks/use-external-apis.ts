// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TanStack Query hooks for the External APIs management surface (Phase API.1 + API.2).
 *
 * - `useExternalAPIs`       — catalogue list; auto-refreshes every 60 s.
 * - `useExternalAPIHealth`  — on-demand health check for a single adapter.
 * - `useExternalAPIToggle`  — mutation to enable/disable an adapter.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import {
  fetchExternalAPIs,
  fetchExternalAPIHealth,
  toggleExternalAPI,
} from "@/lib/api/external-apis"
import type { ExternalAPISummary, ExternalAPIHealth } from "@/lib/types/external-apis"

// ---------------------------------------------------------------------------
// Catalogue list
// ---------------------------------------------------------------------------

export function useExternalAPIs(): {
  data: ExternalAPISummary[] | undefined
  isLoading: boolean
  isError: boolean
  error: Error | null
} {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["external-apis"],
    queryFn: fetchExternalAPIs,
    staleTime: 5 * 60_000,   // 5 min
    refetchInterval: 60_000, // 60 s background refresh
    retry: 1,
  })

  return { data, isLoading, isError, error: error as Error | null }
}

// ---------------------------------------------------------------------------
// Single-adapter health check
// ---------------------------------------------------------------------------

/**
 * On-demand health check for one adapter.
 *
 * @param slug     Adapter slug to check.
 * @param enabled  Set to `false` to skip the query (e.g. before user interaction).
 */
export function useExternalAPIHealth(
  slug: string,
  enabled = false,
): {
  data: ExternalAPIHealth | undefined
  isLoading: boolean
  isError: boolean
  refetch: () => void
} {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["external-api-health", slug],
    queryFn: () => fetchExternalAPIHealth(slug),
    enabled,
    // No background refresh — health is checked manually via the button.
    staleTime: 0,
    retry: 0,
  })

  return { data, isLoading, isError, refetch }
}

// ---------------------------------------------------------------------------
// Toggle mutation
// ---------------------------------------------------------------------------

export function useExternalAPIToggle(): {
  mutate: (args: { slug: string; enabled: boolean }) => void
  isPending: boolean
  error: Error | null
} {
  const queryClient = useQueryClient()

  const { mutate, isPending, error } = useMutation({
    mutationFn: ({ slug, enabled }: { slug: string; enabled: boolean }) =>
      toggleExternalAPI(slug, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["external-apis"] })
    },
  })

  return { mutate, isPending, error: error as Error | null }
}
