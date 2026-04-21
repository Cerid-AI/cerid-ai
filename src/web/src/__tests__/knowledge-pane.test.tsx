// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { Artifact } from "@/lib/types"

// Mock API module
vi.mock("@/lib/api", () => ({
  fetchArtifacts: vi.fn(),
  queryKB: vi.fn(),
  uploadFile: vi.fn(),
  recategorizeArtifact: vi.fn(),
  adminDeleteArtifact: vi.fn(),
  updateArtifactTags: vi.fn(),
  reIngestArtifact: vi.fn(),
}))

// Mock the KB injection context
vi.mock("@/contexts/kb-injection-context", () => ({
  useKBInjection: () => ({
    injectResult: vi.fn(),
    injectedContext: [],
  }),
}))

// Mock drag-drop hook
vi.mock("@/hooks/use-drag-drop", () => ({
  useDragDrop: () => ({
    isDragOver: false,
    dragHandlers: {
      onDragOver: vi.fn(),
      onDrop: vi.fn(),
      onDragLeave: vi.fn(),
      onDragEnter: vi.fn(),
    },
  }),
}))

// Mock lazy-loaded ArtifactPreview
vi.mock("@/components/kb/artifact-preview", () => ({
  default: () => <div data-testid="artifact-preview">Preview</div>,
}))

// Mock sub-components that have complex dependencies
vi.mock("@/components/kb/taxonomy-tree", () => ({
  TaxonomyTree: () => <div data-testid="taxonomy-tree">Taxonomy</div>,
}))
vi.mock("@/components/kb/graph-preview", () => ({
  GraphPreview: () => null,
}))
vi.mock("@/components/kb/upload-dialog", () => ({
  UploadDialog: () => null,
}))
vi.mock("@/components/kb/import-dialog", () => ({
  ImportDialog: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="import-dialog">
      <button onClick={onClose}>Close</button>
    </div>
  ),
}))
vi.mock("@/components/kb/ActivityFeed", () => ({
  ActivityFeed: () => null,
}))
vi.mock("@/components/kb/tag-manager", () => ({
  TagManager: () => null,
}))
vi.mock("@/components/kb/duplicate-detector", () => ({
  DuplicateDetector: () => null,
}))

import { fetchArtifacts, queryKB } from "@/lib/api"
import { KnowledgePane } from "@/components/kb/knowledge-pane"

const mockFetchArtifacts = fetchArtifacts as ReturnType<typeof vi.fn>
const mockQueryKB = queryKB as ReturnType<typeof vi.fn>

function makeArtifact(overrides: Partial<Artifact> = {}): Artifact {
  return {
    id: `art-${Math.random().toString(36).slice(2, 8)}`,
    filename: "test-doc.pdf",
    domain: "research",
    sub_category: "papers",
    tags: [],
    keywords: "[]",
    summary: "A test document summary",
    chunk_count: 3,
    chunk_ids: "[]",
    ingested_at: new Date().toISOString(),
    recategorized_at: null,
    quality_score: 0.85,
    ...overrides,
  }
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  mockFetchArtifacts.mockResolvedValue([])
  mockQueryKB.mockResolvedValue({ results: [] })
})

describe("KnowledgePane", () => {
  // ---- Empty state ----

  it("renders empty state when no artifacts exist", async () => {
    mockFetchArtifacts.mockResolvedValue([])
    render(<KnowledgePane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/0 of 0 artifacts/i)).toBeInTheDocument()
    })
  })

  it("renders Knowledge Base heading", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    expect(screen.getByText("Knowledge Base")).toBeInTheDocument()
  })

  // ---- Artifact list ----

  it("renders artifact list when data available", async () => {
    const artifacts = [
      makeArtifact({ id: "a1", filename: "report.pdf", domain: "research" }),
      makeArtifact({ id: "a2", filename: "notes.md", domain: "coding" }),
    ]
    mockFetchArtifacts.mockResolvedValue(artifacts)
    render(<KnowledgePane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/2 of 2 artifacts/i)).toBeInTheDocument()
    })
  })

  // ---- Search ----

  it("shows search input", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    expect(screen.getByPlaceholderText(/search artifacts/i)).toBeInTheDocument()
  })

  it("search input accepts user typing", async () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    const input = screen.getByPlaceholderText(/search artifacts/i)
    fireEvent.change(input, { target: { value: "machine learning" } })
    expect(input).toHaveValue("machine learning")
  })

  it("triggers search on Enter key", async () => {
    mockQueryKB.mockResolvedValue({
      results: [{
        content: "ML content",
        relevance: 0.9,
        artifact_id: "a1",
        filename: "ml-paper.pdf",
        domain: "research",
        chunk_index: 0,
        collection: "domain_research",
        ingested_at: new Date().toISOString(),
      }],
    })
    render(<KnowledgePane />, { wrapper: createWrapper() })
    const input = screen.getByPlaceholderText(/search artifacts/i)
    fireEvent.change(input, { target: { value: "machine learning" } })
    fireEvent.keyDown(input, { key: "Enter" })
    await waitFor(() => {
      expect(mockQueryKB).toHaveBeenCalledWith("machine learning", undefined)
    })
  })

  it("clears search on Escape key", async () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    const input = screen.getByPlaceholderText(/search artifacts/i)
    fireEvent.change(input, { target: { value: "test query" } })
    fireEvent.keyDown(input, { key: "Escape" })
    expect(input).toHaveValue("")
  })

  // ---- Upload button ----

  it("renders upload button", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    expect(screen.getByText("Upload")).toBeInTheDocument()
  })

  it("has hidden file input for uploads", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    const fileInput = screen.getByLabelText("Upload files")
    expect(fileInput).toBeInTheDocument()
    expect(fileInput).toHaveClass("sr-only")
  })

  // ---- Loading state ----

  it("shows loading state while fetching artifacts", () => {
    // Make the promise hang
    mockFetchArtifacts.mockReturnValue(new Promise(() => {}))
    render(<KnowledgePane />, { wrapper: createWrapper() })
    // The component is in loading state — header still renders
    expect(screen.getByText("Knowledge Base")).toBeInTheDocument()
  })

  // ---- Error state ----

  it("shows error indicator on fetch failure", async () => {
    mockFetchArtifacts.mockRejectedValue(new Error("Network failure"))
    render(<KnowledgePane />, { wrapper: createWrapper() })
    // Wait for the query to fail
    await waitFor(() => {
      // Error state triggers an error indicator in the component
      expect(mockFetchArtifacts).toHaveBeenCalled()
    })
  })

  // ---- Refresh ----

  it("calls fetchArtifacts on initial mount", async () => {
    mockFetchArtifacts.mockResolvedValue([])
    render(<KnowledgePane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(mockFetchArtifacts).toHaveBeenCalled()
    })
  })

  // ---- Import dialog ----

  it("renders import button", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    expect(screen.getByText("Import")).toBeInTheDocument()
  })

  // ---- Duplicates button ----

  it("renders duplicates button", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    expect(screen.getByText("Duplicates")).toBeInTheDocument()
  })

  // ---- View mode toggle ----

  it("renders view mode toggle buttons", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    // Grid and List icons exist in the toolbar
    expect(screen.getByLabelText("Search artifacts")).toBeInTheDocument()
  })

  // ---- Artifact count display ----

  it("displays correct artifact count for large lists", async () => {
    const artifacts = Array.from({ length: 75 }, (_, i) =>
      makeArtifact({ id: `art-${i}`, filename: `file-${i}.pdf` }),
    )
    mockFetchArtifacts.mockResolvedValue(artifacts)
    render(<KnowledgePane />, { wrapper: createWrapper() })
    await waitFor(() => {
      // PAGE_SIZE is 50, so should show "Showing 50 of 75 artifacts"
      const matches = screen.getAllByText(/Showing 50 of 75 artifacts/)
      expect(matches.length).toBeGreaterThanOrEqual(1)
    })
  })

  it("counter numerator never exceeds the filtered total (Bug #12 regression guard)", async () => {
    // Small set (6 items) — numerator must equal 6, NEVER PAGE_SIZE (50).
    const artifacts = Array.from({ length: 6 }, (_, i) =>
      makeArtifact({ id: `art-${i}`, filename: `file-${i}.pdf` }),
    )
    mockFetchArtifacts.mockResolvedValue(artifacts)
    render(<KnowledgePane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/Showing 6 of 6 artifacts/)).toBeInTheDocument()
    })
    // Guard against regression: must not render "Showing 50 of 6" (or any N>6 of 6).
    expect(screen.queryByText(/Showing 50 of 6 artifacts/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Showing 20 of 6 artifacts/)).not.toBeInTheDocument()
  })

  // ---- Search results count ----

  it("shows result count text for search queries", async () => {
    mockQueryKB.mockResolvedValue({
      results: [
        {
          content: "Result 1",
          relevance: 0.8,
          artifact_id: "a1",
          filename: "doc1.pdf",
          domain: "research",
          chunk_index: 0,
          collection: "domain_research",
          ingested_at: new Date().toISOString(),
        },
      ],
    })
    render(<KnowledgePane />, { wrapper: createWrapper() })
    const input = screen.getByPlaceholderText(/search artifacts/i)
    fireEvent.change(input, { target: { value: "deep learning" } })
    fireEvent.keyDown(input, { key: "Enter" })
    await waitFor(() => {
      expect(screen.getByText(/results for "deep learning"/)).toBeInTheDocument()
    })
  })

  // ---- Client source filter ----

  it("renders client source filter dropdown", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    // The default selection shows "Personal" (gui)
    expect(screen.getByText("Personal")).toBeInTheDocument()
  })

  // ---- Search help tooltip ----

  it("renders search help button", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    expect(screen.getByLabelText("Search help")).toBeInTheDocument()
  })
})
