// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// TanStack Query hook for the Cartographer graph-map endpoint.
// staleTime 60s, refetchInterval 75s, keep-previous on refetch.
// Tracks newly-ingested entity ids so CartographerMap can fire a
// one-shot ingest-pulse highlight.

import { useQuery } from "@tanstack/react-query"
import { useCallback, useRef } from "react"
import { fetchGraphMap } from "@/lib/api/graph-map"

export function useGraphMap() {
  const seenIdsRef = useRef<Set<string>>(new Set())
  // Track which ids were NEW on the most recent data update so the scene
  // can draw a one-shot teal pulse ring on them.
  const newIdsRef = useRef<Set<string>>(new Set())

  const queryResult = useQuery({
    queryKey: ["graph-map"],
    queryFn: ({ signal }) => fetchGraphMap(signal),
    staleTime: 60 * 1000,
    refetchInterval: 75 * 1000,
    // Keep the previous frame on screen while a background refetch is in
    // flight so the map doesn't flash on the 75s poll cycle.
    placeholderData: (prev) => prev,
    select: (data) => {
      // Compute which entity ids are new since the last seen set.
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
