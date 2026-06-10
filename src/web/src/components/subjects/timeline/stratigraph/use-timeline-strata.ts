// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// TanStack Query hooks for Stratigraph data. Matches the 75s poll cadence
// of use-graph-map and keeps prior data visible during refetch.

import { useQuery } from "@tanstack/react-query"
import {
  fetchTimelineStrata,
  fetchTimelineTrack,
  type TimelineStrataResponse,
  type TimelineTrackResponse,
} from "@/lib/api/graph"

// Poll at the same 75s interval as the Constellation map
const POLL_MS = 75_000

export interface UseTimelineStrataOptions {
  period: "7d" | "30d" | "90d" | "365d"
  granularity?: "day" | "week" | "month"
  from?: string
  to?: string
}

export function useTimelineStrata(opts: UseTimelineStrataOptions) {
  return useQuery<TimelineStrataResponse>({
    queryKey: ["timeline-strata", opts.period, opts.granularity ?? null, opts.from ?? null, opts.to ?? null],
    queryFn: () =>
      fetchTimelineStrata({ period: opts.period, granularity: opts.granularity, from: opts.from, to: opts.to }),
    staleTime: 60_000,
    refetchInterval: POLL_MS,
    // Keep prior data visible while refetching — no flash on poll
    placeholderData: (prev) => prev,
  })
}

export interface UseTimelineTrackOptions {
  canonicalId: string | null
  from?: string
  to?: string
  /** Only fetch when at event-level zoom or track is expanded */
  enabled: boolean
}

export function useTimelineTrack(opts: UseTimelineTrackOptions) {
  return useQuery<TimelineTrackResponse>({
    queryKey: ["timeline-track", opts.canonicalId, opts.from ?? null, opts.to ?? null],
    queryFn: () => fetchTimelineTrack(opts.canonicalId!, { from: opts.from, to: opts.to }),
    enabled: opts.enabled && opts.canonicalId !== null,
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  })
}
