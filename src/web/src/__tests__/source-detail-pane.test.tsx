// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import React from "react"
import { SourceDetailPane } from "@/components/sources/source-detail-pane"
import type { SourceRecord } from "@/lib/api/sources"

const mockPatchSourceConfig = vi.fn()
const mockPatchSourcePolicy = vi.fn()
const mockDeleteSource = vi.fn()
const mockTestSource = vi.fn()

vi.mock("@/lib/api/sources", () => ({
  deleteSource: (...args: unknown[]) => mockDeleteSource(...args),
  patchSourcePolicy: (...args: unknown[]) => mockPatchSourcePolicy(...args),
  patchSourceConfig: (...args: unknown[]) => mockPatchSourceConfig(...args),
  testSource: (...args: unknown[]) => mockTestSource(...args),
}))

vi.mock("@/components/sources/source-kind-icons", () => ({
  descriptorFor: () => ({ icon: () => null, label: "Test Kind" }),
}))

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function makeSource(overrides: Partial<SourceRecord> = {}): SourceRecord {
  return {
    id: "test:1",
    kind: "github",
    family: "dev",
    display_name: "My Source",
    tier: "core",
    status: "connected",
    config: {},
    sync_cursor: {},
    total_artifacts: 0,
    total_chunks: 0,
    total_edges: 0,
    total_artifacts_24h: 0,
    connection_time_ms: null,
    last_sync_at: null,
    created_at: null,
    last_error: null,
    quality_floor: 0,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockPatchSourceConfig.mockResolvedValue({})
  mockPatchSourcePolicy.mockResolvedValue({})
  mockDeleteSource.mockResolvedValue(undefined)
  mockTestSource.mockResolvedValue({ ok: true, detail: "OK", last_error: null })
})

describe("SourceDetailPane — policy section gating", () => {
  it("renders Apply policy button for non-folder source kinds", () => {
    const source = makeSource({ kind: "github" })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(screen.getByRole("button", { name: /apply policy/i })).toBeInTheDocument()
    expect(screen.queryByText(/retention and quality-floor settings aren't available/i)).not.toBeInTheDocument()
  })

  it("does NOT render Apply policy button for folder kind", () => {
    const source = makeSource({ kind: "folder", id: "folder:1" })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(screen.queryByRole("button", { name: /apply policy/i })).not.toBeInTheDocument()
  })

  it("renders the folder policy caption for folder kind", () => {
    const source = makeSource({ kind: "folder", id: "folder:1" })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(
      screen.getByText(/retention and quality-floor settings aren't available for watched folders yet/i),
    ).toBeInTheDocument()
  })

  it("still renders the Policy section heading for folder kind (non-interactive)", () => {
    const source = makeSource({ kind: "folder", id: "folder:1" })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(screen.getByText("Policy")).toBeInTheDocument()
  })
})

describe("SourceDetailPane — Configuration section", () => {
  it("renders a Configuration section heading for editable kinds (rss)", () => {
    const source = makeSource({ kind: "rss", config: { url: "https://example.com/feed.xml" } })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(screen.getByText("Configuration")).toBeInTheDocument()
  })

  it("renders a Configuration section heading for editable kinds (folder)", () => {
    const source = makeSource({ kind: "folder", id: "folder:cfg", config: { path: "/notes" } })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(screen.getByText("Configuration")).toBeInTheDocument()
  })

  it("does NOT render a Configuration section for non-editable kinds (gmail)", () => {
    const source = makeSource({ kind: "gmail", config: {} })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(screen.queryByText("Configuration")).not.toBeInTheDocument()
  })

  it("does NOT render a Configuration section for non-editable kinds (bookmarks)", () => {
    const source = makeSource({ kind: "bookmarks", config: {} })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(screen.queryByText("Configuration")).not.toBeInTheDocument()
  })

  it("renders the Save button in the Configuration section for rss", () => {
    const source = makeSource({ kind: "rss", config: { url: "https://example.com/feed.xml" } })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(screen.getByRole("button", { name: /^save$/i })).toBeInTheDocument()
  })

  it("calls patchSourceConfig with the changed field when Save is clicked", async () => {
    const source = makeSource({ kind: "rss", id: "rss:1", config: { url: "https://old.example.com/feed.xml" } })
    render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    // Change the URL so the diff is non-empty
    const input = screen.getByLabelText(/feed url/i)
    fireEvent.change(input, { target: { value: "https://new.example.com/feed.xml" } })
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    await waitFor(() => expect(mockPatchSourceConfig).toHaveBeenCalledOnce())
    expect(mockPatchSourceConfig).toHaveBeenCalledWith("rss:1", expect.objectContaining({ url: "https://new.example.com/feed.xml" }))
  })
})

describe("SourceDetailPane — invalidation bug fix", () => {
  it("patchSourcePolicy invalidates ingestion-sources (not the stale sources key)", async () => {
    const source = makeSource({ kind: "rss", id: "rss:2" })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    const invalidate = vi.spyOn(qc, "invalidateQueries")

    render(
      <QueryClientProvider client={qc}>
        <SourceDetailPane open source={source} onClose={() => {}} />
      </QueryClientProvider>,
    )
    fireEvent.click(screen.getByRole("button", { name: /apply policy/i }))
    await waitFor(() => expect(invalidate).toHaveBeenCalled())
    // Must invalidate ingestion-sources, NOT the stale ["sources"] key
    const calls = invalidate.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey)
    expect(calls.some((k) => Array.isArray(k) && k[0] === "ingestion-sources")).toBe(true)
    expect(calls.some((k) => Array.isArray(k) && k[0] === "sources")).toBe(false)
  })

  it("patchSourceConfig invalidation comes from the form only (no double-invalidation from onSaved)", async () => {
    const source = makeSource({ kind: "rss", id: "rss:3", config: { url: "https://example.com/feed.xml" } })
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    const invalidate = vi.spyOn(qc, "invalidateQueries")

    render(
      <QueryClientProvider client={qc}>
        <SourceDetailPane open source={source} onClose={() => {}} />
      </QueryClientProvider>,
    )
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    await waitFor(() => expect(mockPatchSourceConfig).toHaveBeenCalledOnce())
    // Allow the onSuccess microtask to flush
    await waitFor(() => expect(invalidate).toHaveBeenCalled())
    const ingestCalls = invalidate.mock.calls.filter(
      (c) => {
        const k = (c[0] as { queryKey: unknown[] }).queryKey
        return Array.isArray(k) && k[0] === "ingestion-sources"
      },
    )
    // Exactly ONE invalidation from the form's saveMut.onSuccess — not two
    expect(ingestCalls.length).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// axe-clean — one assertion per visually-distinct kind gating branch this
// dialog renders (no fetch cycle; the policy/config gating IS the state).
// ---------------------------------------------------------------------------

describe("SourceDetailPane — axe-clean", () => {
  it("is axe-clean for an editable, policy-enabled kind (rss)", async () => {
    const source = makeSource({ kind: "rss", config: { url: "https://example.com/feed.xml" } })
    const { container } = render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean for the folder kind (no policy, no Apply button)", async () => {
    const source = makeSource({ kind: "folder", id: "folder:1", config: { path: "/notes" } })
    const { container } = render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean for a non-editable-config kind (gmail)", async () => {
    const source = makeSource({ kind: "gmail", config: {} })
    const { container } = render(
      <SourceDetailPane open source={source} onClose={() => {}} />,
      { wrapper: wrap() },
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
