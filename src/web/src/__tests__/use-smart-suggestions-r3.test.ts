// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * E1 M1-3 / R3 — smart-suggestions relative-to-top floor (CR-010 tail).
 *
 * Pre-fix: absolute SUGGESTION_MIN_RELEVANCE = 0.4 dropped ordinal post-rerank
 * hits below 0.4 even when they were the best available matches.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useSmartSuggestions } from "@/hooks/use-smart-suggestions"
import type { KBQueryResult } from "@/lib/types"

vi.mock("@/lib/api", () => ({
  queryKB: vi.fn(),
}))

import { queryKB } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>

const makeHit = (overrides: Partial<KBQueryResult> = {}): KBQueryResult => ({
  content: "chunk",
  relevance: 0.3,
  artifact_id: "art-1",
  filename: "a.md",
  domain: "general",
  chunk_index: 0,
  collection: "kb",
  ingested_at: "2026-01-01T00:00:00Z",
  ...overrides,
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

async function runSearch(
  result: { current: ReturnType<typeof useSmartSuggestions> },
  text: string,
) {
  await act(async () => {
    result.current.debouncedSearch(text)
    await vi.runAllTimersAsync()
  })
  // Flush the queryKB promise settled by the timer callback.
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe("useSmartSuggestions — R3 relative-to-top", () => {
  it("keeps ordinal hits below absolute 0.4 when they are near the top score", async () => {
    mockQueryKB.mockResolvedValue({
      results: [
        makeHit({ artifact_id: "top", relevance: 0.32 }),
        makeHit({ artifact_id: "near", relevance: 0.28 }),
        makeHit({ artifact_id: "weak", relevance: 0.05 }),
      ],
    })

    const { result } = renderHook(() =>
      useSmartSuggestions({ enabled: true, injectedArtifactIds: [], debounceMs: 10 }),
    )

    await runSearch(result, "enough characters to search")

    const ids = result.current.suggestions.map((s) => s.artifact_id)
    // 0.4 * 0.32 = 0.128 floor → top + near survive; weak (0.05) drops
    expect(ids).toContain("top")
    expect(ids).toContain("near")
    expect(ids).not.toContain("weak")
  })

  it("does not drop the sole top hit when its absolute score is below 0.4", async () => {
    mockQueryKB.mockResolvedValue({
      results: [makeHit({ artifact_id: "only", relevance: 0.22 })],
    })

    const { result } = renderHook(() =>
      useSmartSuggestions({ enabled: true, injectedArtifactIds: [], debounceMs: 10 }),
    )

    await runSearch(result, "enough characters to search")

    expect(result.current.suggestions).toHaveLength(1)
    expect(result.current.suggestions[0].artifact_id).toBe("only")
  })
})
