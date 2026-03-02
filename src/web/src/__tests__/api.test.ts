// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

// Mock import.meta.env before importing api module
vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")
vi.stubEnv("VITE_BIFROST_URL", "http://test-bifrost:8080")
vi.stubEnv("VITE_CERID_API_KEY", "test-key-123")

// Must import after env stubbing
const { fetchHealth, fetchArtifacts, queryKB, fetchSettings } =
  await import("@/lib/api")

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  })
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch({}))
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// fetchHealth
// ---------------------------------------------------------------------------

describe("fetchHealth", () => {
  it("calls /health with API key header", async () => {
    const healthData = { status: "healthy", services: {} }
    vi.stubGlobal("fetch", mockFetch(healthData))

    const result = await fetchHealth()
    expect(result).toEqual(healthData)
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/health",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-API-Key": "test-key-123" }),
      }),
    )
  })

  it("throws on non-OK response", async () => {
    vi.stubGlobal("fetch", mockFetch({}, 500))
    await expect(fetchHealth()).rejects.toThrow("Health check failed: 500")
  })
})

// ---------------------------------------------------------------------------
// fetchArtifacts
// ---------------------------------------------------------------------------

describe("fetchArtifacts", () => {
  it("calls /artifacts with default limit", async () => {
    vi.stubGlobal("fetch", mockFetch([]))

    await fetchArtifacts()
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/artifacts?"),
      expect.anything(),
    )
    const url = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain("limit=50")
  })

  it("includes domain filter", async () => {
    vi.stubGlobal("fetch", mockFetch([]))

    await fetchArtifacts("coding", 100)
    const url = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain("domain=coding")
    expect(url).toContain("limit=100")
  })

  it("normalizes string tags to arrays", async () => {
    const artifacts = [
      { id: "1", filename: "test.py", domain: "coding", tags: '["python", "api"]', keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", mockFetch(artifacts))

    const result = await fetchArtifacts()
    expect(result[0].tags).toEqual(["python", "api"])
  })

  it("passes through array tags unchanged", async () => {
    const artifacts = [
      { id: "2", filename: "test.js", domain: "coding", tags: ["js", "react"], keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", mockFetch(artifacts))

    const result = await fetchArtifacts()
    expect(result[0].tags).toEqual(["js", "react"])
  })

  it("handles missing tags gracefully", async () => {
    const artifacts = [
      { id: "3", filename: "test.md", domain: "general", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", mockFetch(artifacts))

    const result = await fetchArtifacts()
    expect(result[0].tags).toEqual([])
  })

  it("handles invalid JSON tags string", async () => {
    const artifacts = [
      { id: "4", filename: "test.txt", domain: "general", tags: "not-json", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", mockFetch(artifacts))

    const result = await fetchArtifacts()
    expect(result[0].tags).toEqual([])
  })

  it("throws on non-OK response", async () => {
    vi.stubGlobal("fetch", mockFetch({}, 503))
    await expect(fetchArtifacts()).rejects.toThrow("Artifacts fetch failed: 503")
  })
})

// ---------------------------------------------------------------------------
// queryKB
// ---------------------------------------------------------------------------

describe("queryKB", () => {
  it("sends POST with correct body", async () => {
    const responseData = { results: [], confidence: 0, total_results: 0, execution_time_ms: 10 }
    vi.stubGlobal("fetch", mockFetch(responseData))

    await queryKB("test query", ["coding", "finance"], 5)
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/agent/query",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          query: "test query",
          domains: ["coding", "finance"],
          top_k: 5,
          use_reranking: true,
          conversation_messages: null,
        }),
      }),
    )
  })

  it("sends null domains when none specified", async () => {
    vi.stubGlobal("fetch", mockFetch({ results: [], confidence: 0, total_results: 0, execution_time_ms: 0 }))

    await queryKB("test query")
    const body = JSON.parse((fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body)
    expect(body.domains).toBeNull()
  })

  it("throws on error response", async () => {
    vi.stubGlobal("fetch", mockFetch({}, 400))
    await expect(queryKB("test")).rejects.toThrow("KB query failed: 400")
  })
})

// ---------------------------------------------------------------------------
// fetchSettings
// ---------------------------------------------------------------------------

describe("fetchSettings", () => {
  it("calls /settings", async () => {
    const settings = { version: "1.0", categorize_mode: "smart" }
    vi.stubGlobal("fetch", mockFetch(settings))

    const result = await fetchSettings()
    expect(result).toEqual(settings)
  })

  it("throws on non-OK response", async () => {
    vi.stubGlobal("fetch", mockFetch({}, 401))
    await expect(fetchSettings()).rejects.toThrow("Settings fetch failed: 401")
  })
})
