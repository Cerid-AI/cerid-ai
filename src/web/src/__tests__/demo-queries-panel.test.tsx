// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockQueryKB = vi.fn()

vi.mock("@/lib/api/kb", () => ({
  queryKB: (...args: unknown[]) => mockQueryKB(...args),
}))

vi.mock("@/lib/log-swallowed", () => ({
  logSwallowedError: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const PYTHON_PACK = {
  id: "python-stdlib-docs",
  name: "Python Standard Library Documentation",
  version: "1.0.0",
  description: "Authoritative Python stdlib reference.",
  domain: "coding",
  sub_category: "python",
  tags: ["python"],
  license: "PSF-2.0",
  size_bytes: 167128,
  artifact_count: 208,
  download_url: "https://example.com/pystd.tar.gz",
  sha256: "ghi789",
  provenance: { status: "built" },
}

const MOCK_KB_ANSWER = "Python's pathlib module provides an object-oriented filesystem interface."

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function renderWithQuery(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

import { DemoQueriesPanel } from "@/components/setup/demo-queries-panel"

const onComplete = vi.fn<() => void>()

beforeEach(() => {
  vi.restoreAllMocks()
  onComplete.mockClear()
  mockQueryKB.mockResolvedValue({
    results: [{ content: MOCK_KB_ANSWER }],
    total_results: 1,
    confidence: 0.95,
  })
})

describe("DemoQueriesPanel", () => {
  it("renders the success banner with pack name and artifact count", () => {
    renderWithQuery(<DemoQueriesPanel pack={PYTHON_PACK} onComplete={onComplete} />)
    expect(screen.getByText(/Python Standard Library Documentation/)).toBeInTheDocument()
    expect(screen.getByText(/installed successfully/i)).toBeInTheDocument()
    expect(screen.getByText(/208 articles/i)).toBeInTheDocument()
  })

  it("renders exactly 3 query buttons", () => {
    renderWithQuery(<DemoQueriesPanel pack={PYTHON_PACK} onComplete={onComplete} />)
    // Each query is a button; "Continue to chat" is also a button
    const queryButtons = screen.getAllByRole("listitem")
    expect(queryButtons).toHaveLength(3)
  })

  it("renders the 'Continue to chat' button", () => {
    renderWithQuery(<DemoQueriesPanel pack={PYTHON_PACK} onComplete={onComplete} />)
    expect(screen.getByRole("button", { name: /Continue to chat/i })).toBeInTheDocument()
  })

  it("calls queryKB when a query button is clicked", async () => {
    renderWithQuery(<DemoQueriesPanel pack={PYTHON_PACK} onComplete={onComplete} />)
    const queryBtn = screen.getByText("How do I read a file with Python's pathlib?")
    fireEvent.click(queryBtn)
    await waitFor(() => expect(mockQueryKB).toHaveBeenCalledTimes(1))
    expect(mockQueryKB).toHaveBeenCalledWith(
      "How do I read a file with Python's pathlib?",
      ["coding"],
      3,
      undefined,
      expect.objectContaining({ useReranking: false, skipCache: true }),
    )
  })

  it("shows the answer text after a query succeeds", async () => {
    renderWithQuery(<DemoQueriesPanel pack={PYTHON_PACK} onComplete={onComplete} />)
    fireEvent.click(screen.getByText("How do I read a file with Python's pathlib?"))
    await waitFor(() => {
      expect(screen.getByText(MOCK_KB_ANSWER)).toBeInTheDocument()
    })
  })

  it("shows an error Alert when the query fails", async () => {
    mockQueryKB.mockRejectedValue(new Error("Query error"))
    renderWithQuery(<DemoQueriesPanel pack={PYTHON_PACK} onComplete={onComplete} />)
    fireEvent.click(screen.getByText("How do I read a file with Python's pathlib?"))
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument()
    })
  })

  it("calls onComplete when 'Continue to chat' is clicked", async () => {
    renderWithQuery(<DemoQueriesPanel pack={PYTHON_PACK} onComplete={onComplete} />)
    fireEvent.click(screen.getByRole("button", { name: /Continue to chat/i }))
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it("uses generic queries for an unlisted pack id", () => {
    const unknownPack = { ...PYTHON_PACK, id: "unknown-pack-xyz", name: "Unknown Pack" }
    renderWithQuery(<DemoQueriesPanel pack={unknownPack} onComplete={onComplete} />)
    // Generic queries should render
    expect(
      screen.getByText("What are the main topics covered in this pack?"),
    ).toBeInTheDocument()
  })

  it("is axe-clean", async () => {
    const { container } = renderWithQuery(
      <DemoQueriesPanel pack={PYTHON_PACK} onComplete={onComplete} />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
