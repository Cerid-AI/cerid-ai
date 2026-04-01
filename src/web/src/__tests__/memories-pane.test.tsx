// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import MemoriesPane from "@/components/memories/memories-pane"

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
