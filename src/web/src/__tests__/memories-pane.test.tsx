// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import type React from "react"
import { render as rtlRender, screen, waitFor } from "@testing-library/react"
import { TooltipProvider } from "@/components/ui/tooltip"
import { axe } from "jest-axe"
import MemoriesPane from "@/components/memories/memories-pane"

// MemoriesPane uses Radix Tooltip (Round 4) — needs a TooltipProvider in the tree.
const render = (ui: React.ReactElement) =>
  rtlRender(<TooltipProvider delayDuration={0}>{ui}</TooltipProvider>)

const mockMemories = [
  {
    id: "mem-1",
    type: "fact",
    content: "The project uses FastAPI with Python 3.11",
    conversation_id: "conv-1",
    created_at: "2026-02-15T10:00:00Z",
    source_filename: "session_1.txt",
  },
  {
    id: "mem-2",
    type: "decision",
    content: "Use ChromaDB for vector storage",
    conversation_id: "conv-2",
    created_at: "2026-02-20T14:00:00Z",
    source_filename: "session_2.txt",
  },
  {
    id: "mem-3",
    type: "preference",
    content: "User prefers dark mode in code editors",
    conversation_id: "conv-3",
    created_at: "2026-03-01T09:00:00Z",
    source_filename: "session_3.txt",
  },
  {
    id: "mem-4",
    type: "action_item",
    content: "Migrate to Pydantic v2 for all models",
    conversation_id: "conv-4",
    created_at: "2026-03-02T16:00:00Z",
    source_filename: "session_4.txt",
  },
]

function mockFetch(data: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("MemoriesPane", () => {
  it("renders loading skeleton initially", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    render(<MemoriesPane />)
    expect(document.querySelector('[class*="animate"]')).toBeTruthy()
  })

  it("renders memory cards after loading", async () => {
    // fetchMemories returns { memories: [...], total: N }
    vi.stubGlobal("fetch", mockFetch({ memories: mockMemories, total: mockMemories.length }))
    render(<MemoriesPane />)
    expect(await screen.findByText(/FastAPI with Python 3.11/)).toBeInTheDocument()
    expect(screen.getByText(/ChromaDB for vector storage/)).toBeInTheDocument()
  })

  it("shows memory type badges", async () => {
    vi.stubGlobal("fetch", mockFetch({ memories: mockMemories, total: mockMemories.length }))
    render(<MemoriesPane />)
    await screen.findByText(/FastAPI/)
    // Legacy types mapped: fact→empirical, action_item→project_context
    // "Empirical" appears in both filter button and card badge, so use getAllByText
    expect(screen.getAllByText("Empirical").length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText("Decision").length).toBeGreaterThanOrEqual(1)
  })

  it("shows empty state when no memories", async () => {
    vi.stubGlobal("fetch", mockFetch({ memories: [], total: 0 }))
    render(<MemoriesPane />)
    await waitFor(() => {
      expect(screen.getByText(/no memories extracted/i)).toBeInTheDocument()
    })
  })

  it("shows filter buttons for all 6 memory types", async () => {
    vi.stubGlobal("fetch", mockFetch({ memories: mockMemories, total: mockMemories.length }))
    render(<MemoriesPane />)
    await screen.findByText(/FastAPI/)
    // 6 memory types — filter button labels
    // Some labels appear in both filter buttons and card badges, so use getAllByText
    const labels = ["Empirical", "Decisions", "Preferences", "Project", "Temporal", "Conversational"]
    for (const label of labels) {
      expect(screen.getAllByText(label).length).toBeGreaterThanOrEqual(1)
    }
  })

  it("shows conversation ID in metadata", async () => {
    vi.stubGlobal("fetch", mockFetch({ memories: mockMemories, total: mockMemories.length }))
    render(<MemoriesPane />)
    await screen.findByText(/FastAPI/)
    // Component shows "conv: {truncatedId}"
    expect(screen.getByText(/conv: conv-1/)).toBeInTheDocument()
  })

  it("has delete buttons for each memory", async () => {
    vi.stubGlobal("fetch", mockFetch({ memories: mockMemories, total: mockMemories.length }))
    render(<MemoriesPane />)
    await screen.findByText(/FastAPI/)
    // Each memory card has edit and delete buttons
    const buttons = screen.getAllByRole("button")
    expect(buttons.length).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// D.2: four-state matrix
// ---------------------------------------------------------------------------

describe("MemoriesPane — four-state matrix (D.2)", () => {
  it("idle/loading: shows Skeleton placeholders while fetching", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { container } = render(<MemoriesPane />)
    // shadcn Skeleton elements rendered
    const skeletons = container.querySelectorAll("[class*=skeleton], [role=status]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("loaded: renders memory cards after data arrives", async () => {
    vi.stubGlobal("fetch", mockFetch({ memories: mockMemories, total: mockMemories.length }))
    render(<MemoriesPane />)
    expect(await screen.findByText(/FastAPI with Python 3.11/)).toBeInTheDocument()
  })

  it("empty: shows empty state when no memories exist", async () => {
    vi.stubGlobal("fetch", mockFetch({ memories: [], total: 0 }))
    render(<MemoriesPane />)
    await waitFor(() => {
      expect(screen.getByText(/no memories extracted/i)).toBeInTheDocument()
    })
  })

  it("error: shows destructive Alert with Retry button on fetch failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network error")))
    render(<MemoriesPane />)
    await waitFor(() => {
      expect(screen.getByText(/Failed to load memories/i)).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// D.3: axe-clean
// ---------------------------------------------------------------------------

describe("MemoriesPane — axe-clean (D.3)", () => {
  it("is axe-clean (D.3) in loading state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { container } = render(<MemoriesPane />)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in empty state", async () => {
    vi.stubGlobal("fetch", mockFetch({ memories: [], total: 0 }))
    const { container } = render(<MemoriesPane />)
    await waitFor(() => screen.getByText(/no memories extracted/i))
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in populated state", async () => {
    vi.stubGlobal("fetch", mockFetch({ memories: mockMemories, total: mockMemories.length }))
    const { container } = render(<MemoriesPane />)
    await screen.findByText(/FastAPI with Python 3.11/)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in error state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("fail")))
    const { container } = render(<MemoriesPane />)
    await waitFor(() => screen.getByText(/Failed to load memories/i))
    expect(await axe(container)).toHaveNoViolations()
  })
})
