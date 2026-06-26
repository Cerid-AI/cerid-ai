// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// TanStack Query hook for the Cartographer graph-map endpoint.
// staleTime 60s, refetchInterval 75s, keep-previous on refetch.
// Tracks newly-ingested entity ids so CartographerMap can fire a
// one-shot ingest-pulse highlight.
//
// Cycle 4: accepts optional layout param; query key includes layout
// so per-layout cache entries are independent in React Query.

import { useQuery } from "@tanstack/react-query"
import { useCallback, useRef } from "react"
import { fetchGraphMap } from "@/lib/api/graph-map"
import type { MapLayout } from "@/lib/graph/cycle4-contracts"

export function useGraphMap(layout?: MapLayout, includeIsolated?: boolean) {
  const seenIdsRef = useRef<Set<string>>(new Set())
  const newIdsRef = useRef<Set<string>>(new Set())

  // Include layout + includeIsolated in query key so each combination
  // has an independent TanStack cache entry and toggling triggers a refetch.
  const queryKey = [
    "graph-map",
    layout && layout !== "force" ? layout : null,
    includeIsolated ? "isolated" : null,
  ].filter(Boolean)

  const queryResult = useQuery({
    queryKey,
    queryFn: ({ signal }) => fetchGraphMap(layout ?? "force", includeIsolated, signal),
    staleTime: 60 * 1000,
    refetchInterval: 75 * 1000,
    placeholderData: (prev) => prev,
    select: (data) => {
      const incoming = new Set(data.entities.map((e) => e.id))
      const newThisCycle: Set<string> = new Set()
      if (seenIdsRef.current.size > 0) {
        for (const id of incoming) {
          if (!seenIdsRef.current.has(id)) {
            newThisCycle.add(id)
          }
        }
      }
      seenIdsRef.current = incoming
      newIdsRef.current = newThisCycle
      return data
    },
  })

  const drainNewIds = useCallback((): Set<string> => {
    const ids = newIdsRef.current
    newIdsRef.current = new Set()
    return ids
  }, [])

  return { ...queryResult, drainNewIds }
}
