// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Contract tests for the wiki normalizer functions.
// These guard the explicit-allowlist normalizers against silent field drops.

import { describe, it, expect, vi, beforeEach } from "vitest"

// Mock the fetch so we can unit-test the normalizer logic without a backend.
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

// Import after stubbing fetch.
import { fetchWikiEntities, fetchWikiEntity } from "../wiki"

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// normalizeEntitySummary — EntitySummary contract
// ---------------------------------------------------------------------------

describe("normalizeEntitySummary — allowlist contract", () => {
  function makeRawSummary(extra: Record<string, unknown> = {}) {
    return {
      canonical_id: "tesla",
      name: "Tesla",
      entity_type: "ORG",
      summary: "A car company.",
      mention_count: 42,
      recent_activity_score: 0.8,
      summary_updated_at: "2026-06-01T00:00:00Z",
      // Extra fields that must NOT be silently dropped
      primary_domain: "research",
      ...extra,
    }
  }

  it("maps canonical_id to slug", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummary()],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.slug).toBe("tesla")
  })

  it("maps primary_domain from raw.primary_domain (explicit, not silent drop)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummary({ primary_domain: "coding" })],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.primary_domain).toBe("coding")
  })

  it("primary_domain is null when absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummary({ primary_domain: undefined })],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.primary_domain).toBeNull()
  })

  it("primary_domain is null when null", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummary({ primary_domain: null })],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.primary_domain).toBeNull()
  })

  it("core fields are preserved (regression guard)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummary()],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.slug).toBe("tesla")
    expect(entity.name).toBe("Tesla")
    expect(entity.entity_type).toBe("ORG")
    expect(entity.related_count).toBe(42)
    expect(entity.last_updated_at).toBe("2026-06-01T00:00:00Z")
  })
})

// ---------------------------------------------------------------------------
// normalizeEntityPage — WikiEntityPage contract
// ---------------------------------------------------------------------------

describe("normalizeEntityPage — allowlist contract", () => {
  function makeRawPage(extra: Record<string, unknown> = {}) {
    return {
      slug: "tesla",
      name: "Tesla",
      entity_type: "ORG",
      summary: "A car company.",
      related_entities: [],
      source_artifacts: [],
      contradictions: [],
      external_references: [],
      last_updated_at: "2026-06-01T00:00:00Z",
      next_refresh_due: null,
      confidence_band: "medium",
      mention_count: 5,
      primary_domain: "research",
      domain_mix: { research: 5, coding: 2 },
      primary_subcategory: "papers",
      ...extra,
    }
  }

  it("maps primary_domain to string", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage(),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.primary_domain).toBe("research")
  })

  it("maps domain_mix from dict", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ domain_mix: { research: 5, coding: 2 } }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.domain_mix).toEqual({ research: 5, coding: 2 })
  })

  it("parses domain_mix from JSON string (old backend compatibility)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ domain_mix: '{"research":5,"coding":2}' }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.domain_mix).toEqual({ research: 5, coding: 2 })
  })

  it("domain_mix is null when absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ domain_mix: undefined }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.domain_mix).toBeNull()
  })

  it("domain_mix is null for malformed JSON string", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ domain_mix: "not-valid-json" }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.domain_mix).toBeNull()
  })

  it("maps primary_subcategory", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ primary_subcategory: "papers" }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.primary_subcategory).toBe("papers")
  })

  it("primary_subcategory is null when absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ primary_subcategory: null }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.primary_subcategory).toBeNull()
  })

  it("returns null on 404", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({}) })
    const page = await fetchWikiEntity("does-not-exist")
    expect(page).toBeNull()
  })

  it("core fields are preserved alongside new domain fields", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage(),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.slug).toBe("tesla")
    expect(page?.name).toBe("Tesla")
    expect(page?.confidence_band).toBe("medium")
    expect(page?.mention_count).toBe(5)
    // domain fields
    expect(page?.primary_domain).toBe("research")
    expect(page?.domain_mix).toBeDefined()
    expect(page?.primary_subcategory).toBe("papers")
  })
})
