// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F368 — the live /system/storage payload reports ChromaDB 0.0 MB against
 * 4,812 chunks and Neo4j 0.0 MB against 21,950 nodes: the probe cannot stat
 * those volumes from the container, and _neo4j_metrics never measures at all.
 * The panel rendered both zeros as measurements, summed them into the budget,
 * and called 6.9% of 2 GB "healthy" — a number computed from Redis and BM25
 * alone, which stays reassuring right up to a disk-full incident.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import SystemCategory from "@/components/settings/categories/system"
import type { ServerSettings } from "@/lib/types"
import type { SettingsCategoryPageProps } from "@/components/settings/categories/page-props"

const mockSettings = {
  feature_tier: "community",
  feature_flags: {},
  domains: ["code"],
  taxonomy: {},
  storage_mode: "extract_only",
  sync_backend: "local",
  machine_id: "test-machine",
  version: "0.9.1",
} as unknown as ServerSettings

const defaultProps: SettingsCategoryPageProps = {
  settings: mockSettings,
  patch: vi.fn().mockResolvedValue({ ok: true }),
  onRefresh: vi.fn(),
}

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

function ok(data: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

// The live payload from the audit baseline.
const UNMEASURED_STORAGE = {
  chromadb: { disk_mb: 0.0, collections: 26, chunks: 4812 },
  neo4j: { disk_mb: 0, nodes: 21950, relationships: 125170 },
  redis: { memory_mb: 114.69, keys: 900, peak_mb: 120 },
  bm25: { disk_mb: 26.52, index_count: 3 },
  total_mb: 141.21,
  limit_mb: 2048,
  usage_pct: 6.9,
  status: "healthy",
}

const MOCK_SYNC_STATUS = {
  sync_dir: "/data/cerid-sync",
  manifest: null,
  local: {
    neo4j_artifacts: 12,
    neo4j_domains: 4,
    neo4j_relationships: 30,
    neo4j_memories: 6,
    neo4j_entities: 9,
    chroma_chunks: { code: 44 },
    redis_entries: 7,
  },
}

function stub(storage: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/system/storage")) return ok(storage)
      if (url.includes("/sync/status")) return ok(MOCK_SYNC_STATUS)
      return ok({})
    }),
  )
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe("Storage gauge — unmeasured stores", () => {
  it("does not report a store holding 4,812 chunks as 0.0 MB", async () => {
    stub(UNMEASURED_STORAGE)
    render(<SystemCategory {...defaultProps} />, { wrapper })
    const chroma = await screen.findByTestId("storage-segment-chromadb")
    expect(chroma).not.toHaveTextContent("0.0 MB")
    expect(chroma).toHaveTextContent("—")
  })

  it("does not report a graph of 21,950 nodes as 0.0 MB", async () => {
    stub(UNMEASURED_STORAGE)
    render(<SystemCategory {...defaultProps} />, { wrapper })
    const neo4j = await screen.findByTestId("storage-segment-neo4j")
    expect(neo4j).not.toHaveTextContent("0.0 MB")
  })

  it("does not call a total healthy when two of four stores are unmeasured", async () => {
    stub(UNMEASURED_STORAGE)
    render(<SystemCategory {...defaultProps} />, { wrapper })
    const badge = await screen.findByTestId("storage-status-badge")
    expect(badge.textContent).not.toBe("healthy")
    expect(badge).toHaveTextContent(/partial/i)
    expect(await screen.findByTestId("storage-total")).toHaveTextContent(/2 of 4/i)
  })

  it("reports a genuinely empty store as 0.0 MB — the control", async () => {
    stub({
      ...UNMEASURED_STORAGE,
      chromadb: { disk_mb: 0.0, collections: 0, chunks: 0 },
      neo4j: { disk_mb: 12.5, nodes: 100, relationships: 40 },
    })
    render(<SystemCategory {...defaultProps} />, { wrapper })
    const chroma = await screen.findByTestId("storage-segment-chromadb")
    expect(chroma).toHaveTextContent("0.0 MB")
    const badge = await screen.findByTestId("storage-status-badge")
    expect(badge).toHaveTextContent("healthy")
  })
})
