// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"

const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

import { fetchWikiIndex, fetchWikiLog, fetchWikiConcept } from "@/lib/api/wiki-browse"

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// fetchWikiIndex
// ---------------------------------------------------------------------------

describe("fetchWikiIndex", () => {
  it("normalizes a plain array response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [
        {
          slug: "other:python",
          name: "Python",
          entity_type: "OTHER",
          one_liner: "A high-level language.",
          last_updated_at: "2026-06-08T04:07:53Z",
          activity_score: 52,
          has_summary: true,
        },
      ],
    })
    const result = await fetchWikiIndex()
    expect(result.entries).toHaveLength(1)
    expect(result.entries[0].slug).toBe("other:python")
    expect(result.entries[0].has_summary).toBe(true)
    expect(result.total).toBeNull()
  })

  it("handles object-envelope response with total", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        items: [
          { slug: "org:acme", name: "Acme", entity_type: "ORG", one_liner: null, last_updated_at: null, activity_score: 0, has_summary: false },
        ],
        total: 42,
      }),
    })
    const result = await fetchWikiIndex({ order: "name" })
    expect(result.entries).toHaveLength(1)
    expect(result.total).toBe(42)
  })

  it("returns has_summary: false for stub entries", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [{ slug: "org:stub", name: "Stub", entity_type: "ORG", one_liner: null, has_summary: false }],
    })
    const result = await fetchWikiIndex()
    expect(result.entries[0].has_summary).toBe(false)
  })

  it("throws on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 })
    await expect(fetchWikiIndex()).rejects.toThrow("Wiki index fetch failed (500)")
  })

  it("passes q and order params to the URL", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] })
    await fetchWikiIndex({ q: "py", order: "name" })
    const calledUrl = mockFetch.mock.calls[0][0] as string
    expect(calledUrl).toContain("q=py")
    expect(calledUrl).toContain("order=name")
  })
})

// ---------------------------------------------------------------------------
// fetchWikiLog
// ---------------------------------------------------------------------------

describe("fetchWikiLog", () => {
  it("normalizes log entries", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [
        {
          log_id: "abc123",
          ts: "2026-06-11T03:39:53Z",
          action: "refresh",
          entity_slug: "other:python",
          summary: "Python is…",
          source_artifact_id: null,
        },
      ],
    })
    const entries = await fetchWikiLog()
    expect(entries).toHaveLength(1)
    expect(entries[0].log_id).toBe("abc123")
    expect(entries[0].action).toBe("refresh")
    expect(entries[0].source_artifact_id).toBeNull()
  })

  it("passes entity_slug param when scoped", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] })
    await fetchWikiLog({ entitySlug: "other:python" })
    const calledUrl = mockFetch.mock.calls[0][0] as string
    expect(calledUrl).toContain("entity_slug=other%3Apython")
  })

  it("throws on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 503 })
    await expect(fetchWikiLog()).rejects.toThrow("Wiki log fetch failed (503)")
  })
})

// ---------------------------------------------------------------------------
// fetchWikiConcept
// ---------------------------------------------------------------------------

describe("fetchWikiConcept", () => {
  it("normalizes a concept page", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        slug: "concept:0:2625",
        name: "Python",
        summary: "A community…",
        member_count: 71,
        level: 0,
        last_updated_at: "2026-06-10T00:00:00Z",
        members: [
          { canonical_id: "other:python", name: "Python", entity_type: "OTHER" },
          { canonical_id: "org:cpython", name: "CPython", entity_type: "ORG" },
        ],
      }),
    })
    const page = await fetchWikiConcept("concept:0:2625")
    expect(page).not.toBeNull()
    expect(page!.slug).toBe("concept:0:2625")
    expect(page!.members).toHaveLength(2)
    expect(page!.members[0].slug).toBe("other:python")
  })

  it("returns null on 404", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 404 })
    const result = await fetchWikiConcept("concept:0:9999")
    expect(result).toBeNull()
  })

  it("throws on non-ok non-404", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 })
    await expect(fetchWikiConcept("concept:0:2625")).rejects.toThrow("Wiki concept fetch failed (500)")
  })
})
