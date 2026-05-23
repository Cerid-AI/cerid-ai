// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Phase M Day 6 — SubjectsViewsSidebar tests.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { SubjectsViewsSidebar } from "@/components/subjects/subjects-views-sidebar"

const mockList = vi.fn()
const mockDelete = vi.fn()

vi.mock("@/lib/api/atlas-views", () => ({
  listAtlasViews: (...a: unknown[]) => mockList(...a),
  deleteAtlasView: (...a: unknown[]) => mockDelete(...a),
}))

const mockFetch = vi.fn()
;(globalThis as { fetch: typeof fetch }).fetch = mockFetch as unknown as typeof fetch

beforeEach(() => {
  mockList.mockReset()
  mockDelete.mockReset()
  mockFetch.mockReset()
})

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>
}

function healthResp(over: Partial<{ pro_unlocked: boolean; free_tier_max_views: number }> = {}) {
  return {
    ok: true,
    json: () => Promise.resolve({
      redis_available: true,
      max_views_per_user: 50,
      free_tier_max_views: 3,
      supported_modes: ["atlas", "constellation", "timeline", "wiki"],
      pro_unlocked: false,
      ...over,
    }),
  }
}

function view(over: Partial<{ view_id: string; name: string; mode: string; entity: string; lenses: string[] }> = {}) {
  return {
    view_id: "vid-1",
    name: "view 1",
    entity: "tesla",
    hops: 2,
    mode: "timeline",
    lenses: [],
    filter: null,
    camera: null,
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    ...over,
  }
}

describe("SubjectsViewsSidebar", () => {
  it("filters list call by mode", async () => {
    mockList.mockResolvedValue([])
    mockFetch.mockResolvedValue(healthResp())
    render(wrap(<SubjectsViewsSidebar mode="timeline" onRestore={vi.fn()} />))
    await waitFor(() => expect(mockList).toHaveBeenCalledWith({ mode: "timeline" }))
  })

  it("renders empty state when no views", async () => {
    mockList.mockResolvedValue([])
    mockFetch.mockResolvedValue(healthResp())
    render(wrap(<SubjectsViewsSidebar mode="wiki" onRestore={vi.fn()} />))
    expect(await screen.findByTestId("subjects-views-empty")).toBeInTheDocument()
  })

  it("calls onRestore when a view is clicked", async () => {
    mockList.mockResolvedValue([view()])
    mockFetch.mockResolvedValue(healthResp())
    const onRestore = vi.fn()
    const user = userEvent.setup()
    render(wrap(<SubjectsViewsSidebar mode="timeline" onRestore={onRestore} />))
    const item = await screen.findByText("view 1")
    await user.click(item)
    expect(onRestore).toHaveBeenCalledWith(expect.objectContaining({ view_id: "vid-1" }))
  })

  it("shows free-tier cap hint when at the limit on community tier", async () => {
    mockList.mockResolvedValue([
      view({ view_id: "1", name: "v1" }),
      view({ view_id: "2", name: "v2" }),
      view({ view_id: "3", name: "v3" }),
    ])
    mockFetch.mockResolvedValue(healthResp({ pro_unlocked: false, free_tier_max_views: 3 }))
    render(wrap(<SubjectsViewsSidebar mode="timeline" onRestore={vi.fn()} />))
    expect(await screen.findByText(/Upgrade to Pro/i)).toBeInTheDocument()
  })

  it("hides cap hint when Pro is unlocked", async () => {
    mockList.mockResolvedValue([
      view({ view_id: "1", name: "pro-1" }),
      view({ view_id: "2", name: "pro-2" }),
      view({ view_id: "3", name: "pro-3" }),
      view({ view_id: "4", name: "pro-4" }),
    ])
    mockFetch.mockResolvedValue(healthResp({ pro_unlocked: true }))
    render(wrap(<SubjectsViewsSidebar mode="timeline" onRestore={vi.fn()} />))
    await screen.findByText("pro-1")
    expect(screen.queryByText(/Upgrade to Pro/i)).not.toBeInTheDocument()
  })
})
