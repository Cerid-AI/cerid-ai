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

  it("maps top_tags from array (Slice 6.3 list seam)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummary({ top_tags: ["python", "docker"] })],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.top_tags).toEqual(["python", "docker"])
  })

  it("parses top_tags from JSON string", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummary({ top_tags: '["python"]' })],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.top_tags).toEqual(["python"])
  })

  it("top_tags is null when absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummary({ top_tags: undefined })],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.top_tags).toBeNull()
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
    expect(entity.mention_count).toBe(42)
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

  it("maps domain_salience from dict, preserving order (Slice 6.1)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ domain_salience: { finance: 45.0, general: 11.25 } }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.domain_salience).toEqual({ finance: 45.0, general: 11.25 })
    expect(Object.keys(page?.domain_salience ?? {})).toEqual(["finance", "general"])
  })

  it("parses domain_salience from JSON string (old backend compatibility)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ domain_salience: '{"finance":45.0,"general":11.25}' }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.domain_salience).toEqual({ finance: 45.0, general: 11.25 })
  })

  it("domain_salience is null when absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ domain_salience: undefined }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.domain_salience).toBeNull()
  })

  it("maps top_tags from array (Slice 6.3)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ top_tags: ["python", "docker", "api"] }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.top_tags).toEqual(["python", "docker", "api"])
  })

  it("parses top_tags from JSON string (old backend compatibility)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ top_tags: '["python","docker"]' }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.top_tags).toEqual(["python", "docker"])
  })

  it("top_tags is null when absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPage({ top_tags: undefined }),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.top_tags).toBeNull()
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

// ---------------------------------------------------------------------------
// normalizeRelatedEntity — new fields: entity_type, display_title, has_summary, one_liner
// ---------------------------------------------------------------------------

describe("normalizeRelatedEntity — amendment fields", () => {
  function makeRawPageWithRelated(related: Record<string, unknown>[]) {
    return {
      slug: "tesla",
      name: "Tesla",
      entity_type: "ORG",
      summary: null,
      related_entities: related,
      source_artifacts: [],
      contradictions: [],
      external_references: [],
      last_updated_at: null,
      next_refresh_due: null,
      confidence_band: "medium",
      mention_count: 1,
      primary_domain: null,
      domain_mix: null,
      primary_subcategory: null,
    }
  }

  it("preserves entity_type (was dropped by old normalizer)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPageWithRelated([
        { canonical_id: "python", name: "Python", co_mention_count: 5, entity_type: "OTHER", has_summary: true, one_liner: "A language." },
      ]),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.related_entities[0].entity_type).toBe("OTHER")
  })

  it("maps has_summary to boolean", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPageWithRelated([
        { canonical_id: "python", name: "Python", co_mention_count: 5, entity_type: "OTHER", has_summary: true, one_liner: "A language." },
      ]),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.related_entities[0].has_summary).toBe(true)
  })

  it("defaults has_summary to false when absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPageWithRelated([
        { canonical_id: "python", name: "Python", co_mention_count: 5, entity_type: "OTHER" },
      ]),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.related_entities[0].has_summary).toBe(false)
  })

  it("maps one_liner when present", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPageWithRelated([
        { canonical_id: "python", name: "Python", co_mention_count: 5, entity_type: "OTHER", has_summary: true, one_liner: "A language." },
      ]),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.related_entities[0].one_liner).toBe("A language.")
  })

  it("maps one_liner to null when absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => makeRawPageWithRelated([
        { canonical_id: "python", name: "Python", co_mention_count: 5, entity_type: "OTHER", has_summary: false },
      ]),
    })
    const page = await fetchWikiEntity("tesla")
    expect(page?.related_entities[0].one_liner).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// normalizeEntitySummary — match_rank passthrough
// ---------------------------------------------------------------------------

describe("normalizeEntitySummary — match_rank passthrough", () => {
  function makeRawSummaryWithRank(rank: unknown) {
    return {
      canonical_id: "python",
      name: "Python",
      entity_type: "OTHER",
      summary: "A language.",
      mention_count: 5,
      recent_activity_score: 0.9,
      summary_updated_at: "2026-06-01T00:00:00Z",
      primary_domain: "coding",
      match_rank: rank,
    }
  }

  it("threads match_rank as number when present", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummaryWithRank(0)],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.match_rank).toBe(0)
  })

  it("match_rank is null when absent (browse results)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawSummaryWithRank(undefined)],
    })
    const [entity] = await fetchWikiEntities()
    expect(entity.match_rank).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// fetchWikiLog — normalizer contract
// ---------------------------------------------------------------------------

import { fetchWikiLog } from "../wiki"

describe("fetchWikiLog — normalizer contract", () => {
  function makeRawLogEntry(extra: Record<string, unknown> = {}) {
    return {
      log_id: "log-001",
      ts: "2026-06-11T03:39:53Z",
      action: "refresh",
      entity_slug: "other:python",
      summary: "Python is a language.",
      source_artifact_id: null,
      ...extra,
    }
  }

  it("maps log_id and ts", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawLogEntry()],
    })
    const [entry] = await fetchWikiLog({ entity_slug: "other:python" })
    expect(entry.log_id).toBe("log-001")
    expect(entry.ts).toBe("2026-06-11T03:39:53Z")
  })

  it("maps action verb", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawLogEntry({ action: "enrich" })],
    })
    const [entry] = await fetchWikiLog({ entity_slug: "other:python" })
    expect(entry.action).toBe("enrich")
  })

  it("source_artifact_id is null when absent", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [makeRawLogEntry({ source_artifact_id: null })],
    })
    const [entry] = await fetchWikiLog({ entity_slug: "other:python" })
    expect(entry.source_artifact_id).toBeNull()
  })

  it("throws on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) })
    await expect(fetchWikiLog({ entity_slug: "other:python" })).rejects.toThrow("500")
  })
})
