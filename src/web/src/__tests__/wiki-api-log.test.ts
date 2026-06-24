// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"

const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

import { fetchWikiLog } from "@/lib/api/wiki"

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// fetchWikiLog (wiki.ts — per-entity history pane, param `entity_slug`)
// ---------------------------------------------------------------------------

describe("fetchWikiLog (wiki.ts)", () => {
  it("normalizes object-envelope { entries, total } response (WK5)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        entries: [
          {
            log_id: "abc123",
            ts: "2026-06-11T03:39:53Z",
            action: "refresh",
            entity_slug: "other:python",
            summary: "Python is…",
            source_artifact_id: null,
          },
        ],
        total: 1,
      }),
    })
    const entries = await fetchWikiLog({ entity_slug: "other:python" })
    expect(entries).toHaveLength(1)
    expect(entries[0].log_id).toBe("abc123")
    expect(entries[0].action).toBe("refresh")
  })

  it("still accepts a plain array response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [
        {
          log_id: "arr1",
          ts: "2026-06-11T03:39:53Z",
          action: "create",
          entity_slug: "other:python",
          summary: null,
          source_artifact_id: null,
        },
      ],
    })
    const entries = await fetchWikiLog({ entity_slug: "other:python" })
    expect(entries).toHaveLength(1)
    expect(entries[0].log_id).toBe("arr1")
  })

  it("passes entity_slug param to the URL", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ entries: [], total: 0 }) })
    await fetchWikiLog({ entity_slug: "other:python" })
    const calledUrl = mockFetch.mock.calls[0][0] as string
    expect(calledUrl).toContain("entity_slug=other%3Apython")
  })
})
