// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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
const MOCK_TOP_SOURCE = {
  content: MOCK_KB_ANSWER,
  relevance: 0.87,
  artifact_id: "art-1",
  filename: "pathlib_intro.md",
  domain: "coding",
  chunk_index: 0,
  collection: "coding",
  ingested_at: "2026-05-26T00:00:00Z",
}

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
  // ``vi.restoreAllMocks`` resets *spies* to their originals but does NOT
  // clear call history on plain ``vi.fn()`` mocks. Without an explicit
  // ``mockClear`` here, ``toHaveBeenCalledTimes(1)`` assertions in later
  // tests count calls from earlier tests in the file.
  mockQueryKB.mockClear()
  mockQueryKB.mockResolvedValue({
    results: [MOCK_TOP_SOURCE],
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
      expect.objectContaining({
        useReranking: false,
        skipCache: true,
        // Pack-scoped retrieval — F-06-01 regression guard.
        // Without ``metadata_filter: { pack_id }`` the demo query bleeds
        // into pre-seeded eval corpora and returns off-topic answers.
        metadataFilter: { pack_id: "python-stdlib-docs" },
      }),
    )
  })

  it("scopes retrieval to the just-installed pack via pack_id (F-06-01)", async () => {
    const irsPack = { ...PYTHON_PACK, id: "irs-publications-curated", domain: "personal" }
    renderWithQuery(<DemoQueriesPanel pack={irsPack} onComplete={onComplete} />)
    fireEvent.click(screen.getByText("What is the standard deduction for a single filer?"))
    await waitFor(() => expect(mockQueryKB).toHaveBeenCalledTimes(1))
    const opts = mockQueryKB.mock.calls[0][4]
    expect(opts.metadataFilter).toEqual({ pack_id: "irs-publications-curated" })
  })

  it("renders source attribution (filename + relevance %) under the answer (F-06-02)", async () => {
    renderWithQuery(<DemoQueriesPanel pack={PYTHON_PACK} onComplete={onComplete} />)
    fireEvent.click(screen.getByText("How do I read a file with Python's pathlib?"))
    await waitFor(() => {
      expect(screen.getByText(MOCK_KB_ANSWER)).toBeInTheDocument()
    })
    // Filename + relevance % must appear so the user can verify provenance.
    expect(screen.getByText("pathlib_intro.md")).toBeInTheDocument()
    expect(screen.getByText("87%")).toBeInTheDocument()
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
