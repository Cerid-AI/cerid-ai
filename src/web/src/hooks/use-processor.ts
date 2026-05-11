// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TanStack Query hooks for the background-job processor subsystem (Phase P.2).
 *
 * Polling cadence:
 *  - Status:  refetch every 5 s  (staleTime 4 s — minimises flicker on tab focus)
 *  - Recent:  refetch every 10 s (jobs complete on the order of seconds–minutes)
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import {
  fetchProcessorStatus,
  fetchProcessorRecent,
  pauseProcessor,
  resumeProcessor,
} from "@/lib/api/processor"
import { logSwallowedError } from "@/lib/log-swallowed"
import type { ProcessorStatus, JobRecord, ProcessorPauseResponse } from "@/lib/types/processor"

// ---------------------------------------------------------------------------
// Status query
// ---------------------------------------------------------------------------

export function useProcessorStatus(): {
  data: ProcessorStatus | undefined
  isLoading: boolean
  isError: boolean
} {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["processor", "status"],
    queryFn: fetchProcessorStatus,
    refetchInterval: 5_000,
    staleTime: 4_000,
    retry: 1,
  })
  return { data, isLoading, isError }
}

// ---------------------------------------------------------------------------
// Recent jobs query
// ---------------------------------------------------------------------------

export function useProcessorRecent(limit = 20): {
  data: JobRecord[] | undefined
  isLoading: boolean
  isError: boolean
} {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["processor", "recent", limit],
    queryFn: () => fetchProcessorRecent(limit),
    refetchInterval: 10_000,
    staleTime: 9_000,
    retry: 1,
  })
  return { data, isLoading, isError }
}

// ---------------------------------------------------------------------------
// Pause / resume mutations
// ---------------------------------------------------------------------------

export interface ProcessorMutations {
  pause: () => Promise<ProcessorPauseResponse>
  resume: () => Promise<ProcessorPauseResponse>
  isPending: boolean
}

export function useProcessorMutations(): ProcessorMutations {
  const queryClient = useQueryClient()

  const pauseMutation = useMutation({
    mutationFn: pauseProcessor,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["processor", "status"] })
    },
    onError: (err) => {
      logSwallowedError(
        err instanceof Error ? err : new Error(String(err)),
        "use-processor.pause",
      )
    },
  })

  const resumeMutation = useMutation({
    mutationFn: resumeProcessor,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["processor", "status"] })
    },
    onError: (err) => {
      logSwallowedError(
        err instanceof Error ? err : new Error(String(err)),
        "use-processor.resume",
      )
    },
  })

  return {
    pause: () => pauseMutation.mutateAsync(),
    resume: () => resumeMutation.mutateAsync(),
    isPending: pauseMutation.isPending || resumeMutation.isPending,
  }
}
