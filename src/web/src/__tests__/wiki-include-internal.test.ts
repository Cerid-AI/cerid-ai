// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * WK2 — the "show internal/client data" toggle threads `include_internal`
 * into the wiki list + index fetchers as a query param.
 *
 * Default (toggle OFF) sends nothing; the server hides the client-data
 * domains. Toggle ON sends `include_internal=true`.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"

const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

import { fetchWikiEntities } from "@/lib/api/wiki"
import { fetchWikiIndex } from "@/lib/api/wiki-browse"

beforeEach(() => {
  vi.clearAllMocks()
})

function calledUrl(): string {
  return String(mockFetch.mock.calls[0][0])
}

// ---------------------------------------------------------------------------
// fetchWikiEntities
// ---------------------------------------------------------------------------

describe("fetchWikiEntities — includeInternal", () => {
  it("omits include_internal by default (toggle off)", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] })
    await fetchWikiEntities()
    expect(calledUrl()).not.toContain("include_internal")
  })

  it("omits include_internal when includeInternal is false", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] })
    await fetchWikiEntities({ includeInternal: false })
    expect(calledUrl()).not.toContain("include_internal")
  })

  it("sends include_internal=true when includeInternal is true", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] })
    await fetchWikiEntities({ includeInternal: true })
    expect(calledUrl()).toContain("include_internal=true")
  })
})

// ---------------------------------------------------------------------------
// fetchWikiIndex
// ---------------------------------------------------------------------------

describe("fetchWikiIndex — includeInternal", () => {
  it("omits include_internal by default (toggle off)", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] })
    await fetchWikiIndex()
    expect(calledUrl()).not.toContain("include_internal")
  })

  it("sends include_internal=true when includeInternal is true", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] })
    await fetchWikiIndex({ includeInternal: true })
    expect(calledUrl()).toContain("include_internal=true")
  })
})
