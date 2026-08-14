// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

// Mock import.meta.env before importing api module
vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")
vi.stubEnv("VITE_BIFROST_URL", "http://test-bifrost:8080")
vi.stubEnv("VITE_CERID_API_KEY", "test-key-123")

// Must import after env stubbing
const {
  fetchHealth, fetchArtifacts, fetchAllArtifacts, queryKB, fetchSettings,
  fetchSyncStatus, triggerSyncExport, triggerSyncImport, fetchArchiveFiles,
} = await import("@/lib/api")

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
  it("calls /health with client ID header", async () => {
    const healthData = { status: "healthy", services: {} }
    vi.stubGlobal("fetch", mockFetch(healthData))

    const result = await fetchHealth()
    expect(result).toEqual(healthData)
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/health",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Client-ID": "gui" }),
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
  it("calls /artifacts with default limit and offset", async () => {
    vi.stubGlobal("fetch", mockFetch([]))

    await fetchArtifacts()
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/artifacts?"),
      expect.anything(),
    )
    const url = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain("limit=50")
    expect(url).toContain("offset=0")
  })

  it("includes domain filter", async () => {
    vi.stubGlobal("fetch", mockFetch([]))

    await fetchArtifacts("coding", 100)
    const url = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain("domain=coding")
    expect(url).toContain("limit=100")
  })

  it("includes sub_category filter when provided", async () => {
    vi.stubGlobal("fetch", mockFetch([]))

    await fetchArtifacts("coding", 100, "algorithms")
    const url = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain("domain=coding")
    expect(url).toContain("sub_category=algorithms")
  })

  it("omits sub_category when not provided", async () => {
    vi.stubGlobal("fetch", mockFetch([]))

    await fetchArtifacts("coding", 100)
    const url = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).not.toContain("sub_category")
  })

  it("includes the offset param for pagination", async () => {
    vi.stubGlobal("fetch", mockFetch([]))

    await fetchArtifacts("coding", 100, undefined, 200)
    const url = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain("offset=200")
  })

  it("normalizes string tags to arrays", async () => {
    const artifacts = [
      { id: "1", filename: "test.py", domain: "coding", tags: '["python", "api"]', keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", mockFetch(artifacts))

    const result = await fetchArtifacts()
    expect(result.artifacts[0].tags).toEqual(["python", "api"])
  })

  it("passes through array tags unchanged", async () => {
    const artifacts = [
      { id: "2", filename: "test.js", domain: "coding", tags: ["js", "react"], keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", mockFetch(artifacts))

    const result = await fetchArtifacts()
    expect(result.artifacts[0].tags).toEqual(["js", "react"])
  })

  it("handles missing tags gracefully", async () => {
    const artifacts = [
      { id: "3", filename: "test.md", domain: "general", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", mockFetch(artifacts))

    const result = await fetchArtifacts()
    expect(result.artifacts[0].tags).toEqual([])
  })

  it("handles invalid JSON tags string", async () => {
    const artifacts = [
      { id: "4", filename: "test.txt", domain: "general", tags: "not-json", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", mockFetch(artifacts))

    const result = await fetchArtifacts()
    expect(result.artifacts[0].tags).toEqual([])
  })

  it("throws on non-OK response", async () => {
    vi.stubGlobal("fetch", mockFetch({}, 503))
    await expect(fetchArtifacts()).rejects.toThrow("Artifacts fetch failed: 503")
  })

  it("falls back to offset+length as total when X-Total-Count header is absent", async () => {
    const artifacts = [
      { id: "1", filename: "a.md", domain: "general", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", mockFetch(artifacts))

    const result = await fetchArtifacts("general", 50, undefined, 0)
    expect(result.total).toBe(1)
    expect(result.hasMore).toBe(false)
  })

  it("reads total and hasMore from response headers when present", async () => {
    const artifacts = [
      { id: "1", filename: "a.md", domain: "general", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" },
    ]
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(artifacts),
      text: () => Promise.resolve(JSON.stringify(artifacts)),
      headers: new Headers({ "X-Total-Count": "350", "X-Has-More": "true" }),
    }))

    const result = await fetchArtifacts("general", 1, undefined, 0)
    expect(result.total).toBe(350)
    expect(result.hasMore).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// fetchAllArtifacts — WB-24: walks pages via offset until hasMore is false,
// instead of the removed single hardcoded-limit=200 fetch.
// ---------------------------------------------------------------------------

describe("fetchAllArtifacts", () => {
  function pagedFetch(pages: unknown[][], total: number) {
    let call = 0
    return vi.fn().mockImplementation(() => {
      const page = pages[call] ?? []
      call += 1
      const offset = pages.slice(0, call - 1).reduce((n, p) => n + p.length, 0)
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(page),
        text: () => Promise.resolve(JSON.stringify(page)),
        headers: new Headers({
          "X-Total-Count": String(total),
          "X-Has-More": String(offset + page.length < total),
        }),
      })
    })
  }

  it("stops after one page when hasMore is false", async () => {
    const items = [{ id: "1", filename: "a.md", domain: "general", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" }]
    vi.stubGlobal("fetch", pagedFetch([items], 1))

    const result = await fetchAllArtifacts("general", undefined, 200)
    expect(result.artifacts).toHaveLength(1)
    expect(result.total).toBe(1)
  })

  it("walks multiple pages until the backend total is reached", async () => {
    const page1 = Array.from({ length: 200 }, (_, i) => ({ id: `p1-${i}`, filename: `f${i}.md`, domain: "general", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" }))
    const page2 = Array.from({ length: 50 }, (_, i) => ({ id: `p2-${i}`, filename: `g${i}.md`, domain: "general", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" }))
    vi.stubGlobal("fetch", pagedFetch([page1, page2], 250))

    const result = await fetchAllArtifacts("general", undefined, 200)
    expect(result.artifacts).toHaveLength(250)
    expect(result.total).toBe(250)
  })

  it("stops at maxItems even if the backend still reports hasMore", async () => {
    const page = Array.from({ length: 200 }, (_, i) => ({ id: `x-${i}`, filename: `x${i}.md`, domain: "general", keywords: "[]", summary: "", chunk_count: 1, chunk_ids: "[]", ingested_at: "2026-01-01" }))
    // Every page reports a huge total so hasMore never flips false — the
    // maxItems safety cap must still terminate the loop.
    vi.stubGlobal("fetch", pagedFetch([page, page, page], 1_000_000))

    const result = await fetchAllArtifacts("general", undefined, 200, 400)
    expect(result.artifacts.length).toBeLessThanOrEqual(400)
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

// ---------------------------------------------------------------------------
// Sync API
// ---------------------------------------------------------------------------

describe("fetchSyncStatus", () => {
  it("calls /sync/status", async () => {
    const statusData = { sync_dir: "/sync", manifest: null, local: {}, sync: {}, diff: {} }
    vi.stubGlobal("fetch", mockFetch(statusData))

    const result = await fetchSyncStatus()
    expect(result).toEqual(statusData)
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/sync/status",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Client-ID": "gui" }),
      }),
    )
  })

  it("throws on error", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "No sync dir" }, 500))
    await expect(fetchSyncStatus()).rejects.toThrow("No sync dir")
  })
})

describe("triggerSyncExport", () => {
  it("sends POST to /sync/export", async () => {
    const exportResult = { neo4j: { artifacts: 10 }, chroma: {}, bm25: {}, redis: 5, tombstones: 0, manifest: {} }
    vi.stubGlobal("fetch", mockFetch(exportResult))

    const result = await triggerSyncExport({ domains: ["coding"] })
    expect(result).toEqual(exportResult)
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/sync/export",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ domains: ["coding"] }),
      }),
    )
  })

  it("sends empty body when no options", async () => {
    vi.stubGlobal("fetch", mockFetch({}))

    await triggerSyncExport()
    const body = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body
    expect(body).toBe("{}")
  })
})

describe("triggerSyncImport", () => {
  it("sends POST with conflict strategy", async () => {
    const importResult = { neo4j: { artifacts_created: 5 }, chroma: {}, bm25: {}, redis: 0, tombstones: 0, consistency_warnings: [] }
    vi.stubGlobal("fetch", mockFetch(importResult))

    await triggerSyncImport({ conflict_strategy: "local_wins" })
    const body = JSON.parse((fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body)
    expect(body.conflict_strategy).toBe("local_wins")
  })

  it("throws on error", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "Merge conflict" }, 500))
    await expect(triggerSyncImport()).rejects.toThrow("Merge conflict")
  })
})

// ---------------------------------------------------------------------------
// Archive API
// ---------------------------------------------------------------------------

describe("fetchArchiveFiles", () => {
  it("calls /archive/files without domain filter", async () => {
    const data = { files: [], total: 0, storage_mode: "extract_only" }
    vi.stubGlobal("fetch", mockFetch(data))

    const result = await fetchArchiveFiles()
    expect(result).toEqual(data)
    expect(fetch).toHaveBeenCalledWith(
      "http://test-mcp:8888/archive/files",
      expect.anything(),
    )
  })

  it("includes domain filter in URL", async () => {
    vi.stubGlobal("fetch", mockFetch({ files: [], total: 0, storage_mode: "archive" }))

    await fetchArchiveFiles("coding")
    const url = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain("domain=coding")
  })

  it("throws on error", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "Not found" }, 404))
    await expect(fetchArchiveFiles()).rejects.toThrow("Not found")
  })
})
