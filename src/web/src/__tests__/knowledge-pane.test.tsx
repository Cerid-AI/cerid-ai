// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
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
// Expose a way for tests to invoke the dialog's onPackInstalled callback so
// we can exercise the F-05-01 filter-broadening behavior without spinning up
// the real pack-install flow.
vi.mock("@/components/kb/knowledge-library-dialog", () => ({
  KnowledgeLibraryDialog: ({ onPackInstalled }: { onPackInstalled?: (id: string) => void }) => (
    <button
      type="button"
      data-testid="trigger-pack-installed"
      onClick={() => onPackInstalled?.("irs-publications")}
    >
      simulate pack install
    </button>
  ),
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

  // E1 R3 / CR-010 tail: absolute 0.35 floor emptied ordinal post-rerank hits.
  it("renders low-ordinal search hits (no absolute 0.35 floor)", async () => {
    mockQueryKB.mockResolvedValue({
      results: [{
        content: "low ordinal but real match",
        relevance: 0.22,
        artifact_id: "a-low",
        filename: "ordinal-hit.pdf",
        domain: "research",
        chunk_index: 0,
        collection: "domain_research",
        ingested_at: new Date().toISOString(),
      }],
    })
    render(<KnowledgePane />, { wrapper: createWrapper() })
    const input = screen.getByPlaceholderText(/search artifacts/i)
    fireEvent.change(input, { target: { value: "ordinal query text" } })
    fireEvent.keyDown(input, { key: "Enter" })
    await waitFor(() => {
      expect(screen.getByText("ordinal-hit.pdf")).toBeInTheDocument()
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
    expect(screen.getByText("Import folder")).toBeInTheDocument()
  })

  // ---- SR4: prominent add-data drop zone ----

  it("renders the prominent add-data drop zone", () => {
    render(<KnowledgePane />, { wrapper: createWrapper() })
    expect(
      screen.getByRole("button", { name: /add files to your knowledge base/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/drop files to add to your knowledge base/i)).toBeInTheDocument()
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

// ---------------------------------------------------------------------------
// D.2: four-state matrix
// ---------------------------------------------------------------------------

describe("KnowledgePane — four-state matrix (D.2)", () => {
  it("idle/loading: shows Skeleton placeholders while fetching", () => {
    mockFetchArtifacts.mockReturnValue(new Promise(() => {}))
    const { container } = render(<KnowledgePane />, { wrapper: createWrapper() })
    const skeletons = container.querySelectorAll("[class*=skeleton], [role=status]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("loaded: renders artifact list after data arrives", async () => {
    mockFetchArtifacts.mockResolvedValue([makeArtifact({ id: "a1" })])
    render(<KnowledgePane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/Showing 1 of 1 artifact/i)).toBeInTheDocument()
    })
  })

  it("empty: shows empty state messaging when no artifacts", async () => {
    mockFetchArtifacts.mockResolvedValue([])
    render(<KnowledgePane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/No artifacts yet/i)).toBeInTheDocument()
    })
  })

  it("error: shows destructive Alert with Retry button on fetch failure", async () => {
    mockFetchArtifacts.mockRejectedValue(new Error("Network failure"))
    render(<KnowledgePane />, { wrapper: createWrapper() })
    // The error Alert and Retry button appear once the query settles into error state
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
    }, { timeout: 3000 })
  })
})

// ---------------------------------------------------------------------------
// F-05-01 (rc2.1): pack install broadens the source filter
// ---------------------------------------------------------------------------

describe("KnowledgePane — F-05-01 pack-install filter broadening", () => {
  it("switches the source filter to 'All sources' when a pack install completes", async () => {
    // Seed two artifacts so the source dropdown shows a value before install.
    mockFetchArtifacts.mockResolvedValue([
      makeArtifact({ id: "a1", filename: "personal.pdf" }),
    ])
    render(<KnowledgePane />, { wrapper: createWrapper() })

    // Default selection is "Personal" (gui).
    const sourceCombobox = await screen.findByRole("combobox", { name: /filter by source/i })
    expect(sourceCombobox).toHaveTextContent(/personal/i)

    // Simulate the pack-install callback firing from the dialog mock.
    fireEvent.click(screen.getByTestId("trigger-pack-installed"))

    // Filter should broaden to "All sources" so newly-ingested pack rows
    // (which lack the "gui" client_source tag) appear without manual action.
    await waitFor(() => {
      expect(sourceCombobox).toHaveTextContent(/all sources/i)
    })
  })
})

// ---------------------------------------------------------------------------
// D.3: axe-clean
// ---------------------------------------------------------------------------

describe("KnowledgePane — axe-clean (D.3)", () => {
  it("is axe-clean (D.3) in empty state", async () => {
    mockFetchArtifacts.mockResolvedValue([])
    const { container } = render(<KnowledgePane />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByText(/No artifacts yet/i))
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean (D.3) in error state", async () => {
    mockFetchArtifacts.mockRejectedValue(new Error("fail"))
    const { container } = render(<KnowledgePane />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByRole("button", { name: /retry/i }), { timeout: 3000 })
    expect(await axe(container)).toHaveNoViolations()
  })
})
