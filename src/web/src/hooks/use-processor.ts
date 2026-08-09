// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * TanStack Query hooks for the background-job processor subsystem (Phase P.2).
 *
 * Polling cadence:
 *  - Status:  refetch every 5 s  (staleTime 4 s — minimises flicker on tab focus)
 *  - Recent:  refetch every 10 s (jobs complete on the order of seconds–minutes)
 */

import {
  useQuery,
  useMutation,
  useQueryClient,
  type QueryObserverResult,
} from "@tanstack/react-query"
import {
  fetchProcessorStatus,
  fetchProcessorRecent,
  pauseProcessor,
  resumeProcessor,
} from "@/lib/api/processor"
import { updateSettings } from "@/lib/api/settings"
import { logSwallowedError } from "@/lib/log-swallowed"
import type { ProcessorStatus, JobRecord, ProcessorPauseResponse, ProcessorMode } from "@/lib/types/processor"

// ---------------------------------------------------------------------------
// Status query
// ---------------------------------------------------------------------------

export function useProcessorStatus(): {
  data: ProcessorStatus | undefined
  isLoading: boolean
  isError: boolean
  refetch: () => Promise<QueryObserverResult<ProcessorStatus, Error>>
} {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["processor", "status"],
    queryFn: fetchProcessorStatus,
    refetchInterval: 5_000,
    staleTime: 4_000,
    retry: 1,
  })
  return { data, isLoading, isError, refetch }
}

// ---------------------------------------------------------------------------
// Recent jobs query
// ---------------------------------------------------------------------------

export function useProcessorRecent(limit = 20): {
  data: JobRecord[] | undefined
  isLoading: boolean
  isError: boolean
  refetch: () => Promise<QueryObserverResult<JobRecord[], Error>>
} {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["processor", "recent", limit],
    queryFn: () => fetchProcessorRecent(limit),
    refetchInterval: 10_000,
    staleTime: 9_000,
    retry: 1,
  })
  return { data, isLoading, isError, refetch }
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

// ---------------------------------------------------------------------------
// Processor mode / monthly cap mutation (Task 2.5d)
// ---------------------------------------------------------------------------
//
// Reuses the existing PATCH /settings client (`updateSettings`, also used by
// the Settings → Privacy panel's `sensitive_domain_retrieval` toggle) rather
// than adding a second settings-PATCH client.

export interface ProcessorSettingsMutation {
  updateMode: (mode: ProcessorMode) => void
  isPending: boolean
}

export function useProcessorSettingsMutation(): ProcessorSettingsMutation {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (mode: ProcessorMode) => updateSettings({ processor_mode: mode }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["processor", "status"] })
      void queryClient.invalidateQueries({ queryKey: ["settings"] })
    },
    onError: (err) => {
      logSwallowedError(
        err instanceof Error ? err : new Error(String(err)),
        "use-processor.updateMode",
      )
    },
  })

  return {
    // Fire-and-forget: failures surface via onError above, so callers never
    // receive a rejecting promise (a bare `void mutateAsync()` would leak an
    // unhandled rejection on network/backend failure).
    updateMode: (mode) => {
      mutation.mutate(mode)
    },
    isPending: mutation.isPending,
  }
}
