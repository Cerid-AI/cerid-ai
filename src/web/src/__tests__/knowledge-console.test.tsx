// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const mockGoTo = vi.fn()
vi.mock("@/contexts/navigation-context", async (orig) => ({
  ...(await orig<typeof import("@/contexts/navigation-context")>()),
  useNavigation: () => ({
    activePane: "chat",
    goTo: mockGoTo,
    composeChat: vi.fn(),
    consumeChatSeed: () => null,
    navVersion: 0,
  }),
}))

import { KnowledgeConsole } from "@/components/kb/knowledge-console"

// KnowledgeConsole uses useQuery (DataSourceIndicator + IngestionProgress).
// Wrap in QueryClientProvider to satisfy TanStack Query context requirement.
function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

// Minimal props: KnowledgeConsoleProps = UseOrchestratedQueryReturn + { ragMode, onRagModeChange?, onClose }
// All required fields from UseOrchestratedQueryReturn filled with inert defaults.
function baseProps(over: Record<string, unknown> = {}) {
  return {
    // UseOrchestratedQueryReturn required fields
    results: [],
    confidence: 0,
    totalResults: 0,
    executionTime: 0,
    isLoading: false,
    error: null,
    isError: false,
    refetch: vi.fn(),
    hasQueried: false,
    sourceBreakdown: null,
    kbSources: [],
    memorySources: [],
    externalSources: [],
    degradedReason: "",
    kbEnabled: true,
    memoryEnabled: true,
    externalEnabled: true,
    toggleKB: vi.fn(),
    toggleMemory: vi.fn(),
    toggleExternal: vi.fn(),
    activeDomains: new Set<string>(),
    toggleDomain: vi.fn(),
    manualQuery: "",
    setManualQuery: vi.fn(),
    executeManualSearch: vi.fn(),
    clearManualSearch: vi.fn(),
    injectedContext: [],
    injectResult: vi.fn(),
    removeInjected: vi.fn(),
    clearInjected: vi.fn(),
    // KnowledgeConsoleProps extra fields
    ragMode: "smart" as const,
    onClose: vi.fn(),
    ...over,
  }
}

beforeEach(() => vi.clearAllMocks())

describe("KnowledgeConsole CH7 controls", () => {
  it("renders domain filter chips and a manual-search input", () => {
    render(<KnowledgeConsole {...baseProps()} />, { wrapper })
    // DomainFilter section rendered with a labelled group
    expect(screen.getByRole("group", { name: /domain filter/i })).toBeInTheDocument()
    // Manual-search input
    expect(screen.getByPlaceholderText(/search knowledge/i)).toBeInTheDocument()
  })

  it("toggling a domain chip calls toggleDomain", () => {
    const toggleDomain = vi.fn()
    render(<KnowledgeConsole {...baseProps({ toggleDomain })} />, { wrapper })
    // Domain badges have role="button" and aria-pressed
    const chips = screen.getAllByRole("button", { pressed: false })
    // Find a domain chip (not the close/run-search/clear buttons)
    const domainChip = chips.find((el) => el.className.includes("capitalize"))
    expect(domainChip).toBeDefined()
    fireEvent.click(domainChip!)
    expect(toggleDomain).toHaveBeenCalled()
  })

  it("pressing Enter in the manual-search input calls executeManualSearch", () => {
    const executeManualSearch = vi.fn()
    render(
      <KnowledgeConsole {...baseProps({ manualQuery: "hello world", executeManualSearch })} />,
      { wrapper },
    )
    const input = screen.getByPlaceholderText(/search knowledge/i)
    fireEvent.keyDown(input, { key: "Enter" })
    expect(executeManualSearch).toHaveBeenCalled()
  })

  it("pressing Escape in the manual-search input calls clearManualSearch", () => {
    const clearManualSearch = vi.fn()
    render(
      <KnowledgeConsole {...baseProps({ manualQuery: "something", clearManualSearch })} />,
      { wrapper },
    )
    const input = screen.getByPlaceholderText(/search knowledge/i)
    fireEvent.keyDown(input, { key: "Escape" })
    expect(clearManualSearch).toHaveBeenCalled()
  })

  it("shows a clear button only when manualQuery is non-empty", () => {
    const { rerender } = render(<KnowledgeConsole {...baseProps()} />, { wrapper })
    expect(screen.queryByRole("button", { name: /clear search/i })).toBeNull()

    rerender(<KnowledgeConsole {...baseProps({ manualQuery: "hello" })} />)
    expect(screen.getByRole("button", { name: /clear search/i })).toBeInTheDocument()
  })

  it("is axe-clean", async () => {
    const { container } = render(<KnowledgeConsole {...baseProps()} />, { wrapper })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("CR-010: relevance footer shows relative ranking, not an aggregate %", () => {
    // `confidence` is the mean of ordinal post-rerank scores, so rendering it as
    // "50%" was miscalibrated. The footer now shows a relative bar labelled
    // "ranked by match" with no absolute aggregate percentage.
    render(
      <KnowledgeConsole
        {...baseProps({
          hasQueried: true,
          confidence: 0.5,
          kbSources: [{
            content: "grounded chunk", relevance: 0.28, artifact_id: "a1",
            filename: "grounded.py", domain: "coding", chunk_index: 0,
            collection: "kb_coding", ingested_at: "2026-01-15T10:00:00Z",
          }],
        })}
      />,
      { wrapper },
    )
    expect(screen.getByText(/ranked by match/i)).toBeInTheDocument()
    // The aggregate "50%" confidence badge is gone (0.5 → "50%" under the old code).
    expect(screen.queryByText("50%")).toBeNull()
  })
})

describe("KnowledgeConsole — data-source indicator (P0-C.4)", () => {
  function stubDataSourcesFetch() {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/data-sources")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            sources: [
              { name: "wikipedia", description: "", enabled: true, configured: true, requires_api_key: false, api_key_env_var: "", domains: [] },
            ],
            total: 1,
          }),
          text: () => Promise.resolve("{}"),
        })
      }
      // Keep IngestionProgress inert (it expects total_files/files).
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ total_files: 0, files: [] }), text: () => Promise.resolve("{}") })
    }))
  }

  it("lists sources read-only — no inline enable/disable switches", async () => {
    stubDataSourcesFetch()
    render(<KnowledgeConsole {...baseProps({ hasQueried: true })} />, { wrapper })
    // External section is collapsed by default — expand it.
    fireEvent.click(await screen.findByText("External"))
    expect(await screen.findByText("wikipedia")).toBeInTheDocument()
    // The source row carries no switch; toggles live in Settings → Extensions.
    const row = screen.getByText("wikipedia").closest("div")!.parentElement!
    expect(row.querySelector("[role='switch']")).toBeNull()
  })

  it("Manage link routes to Settings → Extensions (unified Knowledge Providers)", async () => {
    stubDataSourcesFetch()
    render(<KnowledgeConsole {...baseProps({ hasQueried: true })} />, { wrapper })
    fireEvent.click(await screen.findByText("External"))
    const manage = await screen.findByRole("button", { name: /manage knowledge providers in settings/i })
    fireEvent.click(manage)
    expect(mockGoTo).toHaveBeenCalledWith("settings", expect.objectContaining({ category: "extensions" }))
  })
})

describe("KnowledgeConsole honest degradation (UX-01/UX-02)", () => {
  it("renders the degraded banner when the envelope carries degraded_reason", () => {
    render(
      <KnowledgeConsole
        {...baseProps({
          hasQueried: true,
          degradedReason: "Retrieval took longer than the configured budget.",
        })}
      />,
      { wrapper },
    )
    expect(screen.getByText(/retrieval budget exceeded/i)).toBeInTheDocument()
    expect(
      screen.getByText(/took longer than the configured budget/i),
    ).toBeInTheDocument()
  })

  it("renders no banner when retrieval was not degraded", () => {
    render(<KnowledgeConsole {...baseProps({ hasQueried: true })} />, { wrapper })
    expect(screen.queryByText(/retrieval budget exceeded/i)).toBeNull()
  })
})

// The backend's quality and endorsement boosts multiply a score that is
// already near 1, so a top hit can arrive above 1.0.
describe("KnowledgeConsole relevance figures (audit 50)", () => {
  it("shows each source's relevance as a bar relative to the best in its section", () => {
    render(
      <KnowledgeConsole
        {...baseProps({
          hasQueried: true,
          kbSources: [
            { artifact_id: "a1", chunk_index: 0, filename: "best.md", domain: "general", content: "x", relevance: 0.84 },
            { artifact_id: "a2", chunk_index: 0, filename: "other.md", domain: "general", content: "y", relevance: 0.42 },
          ],
          memorySources: [{ memory_id: "m1", content: "remembered", summary: "remembered", memory_type: "empirical", relevance: 0.3, age_days: 2 }],
          externalSources: [{ content: "external", source_name: "Web", relevance: 0.2 }],
        })}
      />,
      { wrapper },
    )
    fireEvent.click(screen.getByText("External"))
    expect(screen.getAllByRole("progressbar", { name: "Relevance: 1 of 2 results, 1.00 relative to the best match" })).toHaveLength(1)
    expect(screen.getByRole("progressbar", { name: "Relevance: 2 of 2 results, 0.50 relative to the best match" })).toHaveAttribute("aria-valuenow", "50")
    expect(screen.getAllByRole("progressbar", { name: "Relevance: 1 of 1 result, 1.00 relative to the best match" })).toHaveLength(2)
    expect(screen.queryByText(/^\d+%$/)).toBeNull()
  })

  it("never draws a bar past full for a score above 1.0", () => {
    render(
      <KnowledgeConsole
        {...baseProps({
          hasQueried: true,
          kbSources: [{ artifact_id: "a1", chunk_id: "c1", filename: "audit-upload-7Q4.md", domain: "general", content: "x", relevance: 1.02 }],
          memorySources: [{ memory_id: "m1", content: "remembered", summary: "remembered", memory_type: "empirical", relevance: 1.3, age_days: 2 }],
          externalSources: [{ content: "external", source_name: "Web", relevance: 1.1 }],
        })}
      />,
      { wrapper },
    )
    expect(screen.getByText("audit-upload-7Q4.md")).toBeInTheDocument()
    fireEvent.click(screen.getByText("External"))
    const bars = screen.getAllByRole("progressbar", { name: /^Relevance: 1 of 1 result,/ })
    expect(bars).toHaveLength(3)
    expect(bars.map((b) => b.getAttribute("aria-valuenow"))).toEqual(["100", "100", "100"])
  })
})

describe("KnowledgeConsole forgetting", () => {
  it("offers KB passages and memories, naming a verified memory by its node", async () => {
    const api = await import("@/lib/api")
    const preview = vi.spyOn(api, "previewForgetItems").mockResolvedValue({
      subject: null, title: "", groups: [], derived_facts: 0, notes: [], out_of_reach: [],
    })
    const aid = "a".repeat(64)
    render(<KnowledgeConsole {...baseProps({
      hasQueried: true,
      kbSources: [{
        content: "x", relevance: 0.8, artifact_id: aid, filename: "plan.md", domain: "projects",
        chunk_index: 0, chunk_id: `${aid}_0123456789abcdef`, collection: "domain_projects", ingested_at: "",
      }],
      memorySources: [
        { content: "Prefers tea", relevance: 0.6, memory_type: "preference", age_days: 3, summary: "Prefers tea",
          memory_id: "m".repeat(64), source_authority: 0.7, base_similarity: 0.6, access_count: 0,
          source_type: "memory", forget_kind: "artifact" },
        { content: "The limit is $23,500", relevance: 0.5, memory_type: "empirical", age_days: 1, summary: "",
          memory_id: "11111111-2222-3333-4444-555555555555", source_authority: 0.9, base_similarity: 0.5,
          access_count: 0, source_type: "memory", forget_kind: "memory" },
      ],
      externalSources: [{ content: "web", relevance: 0.4, source_url: "https://example.com", source_type: "external" }],
    })} />, { wrapper })

    fireEvent.click(screen.getByRole("button", { name: "Select" }))
    fireEvent.click(screen.getByRole("button", { name: "Select all" }))
    fireEvent.click(screen.getByRole("button", { name: "Forget selected (3)" }))
    await vi.waitFor(() => expect(preview).toHaveBeenCalled())
    expect(preview.mock.calls[0][0]).toEqual([
      { kind: "chunk", id: `${aid}_0123456789abcdef` },
      { kind: "artifact", id: "m".repeat(64) },
      { kind: "memory", id: "11111111-2222-3333-4444-555555555555" },
    ])
    preview.mockRestore()
  })
})

describe("KnowledgeConsole selection across answers", () => {
  it("drops choices made among the previous answer's sources", () => {
    const mem = (id: string) => ({
      content: id, relevance: 0.5, memory_type: "preference", age_days: 1, summary: id, memory_id: id,
      source_authority: 0.7, base_similarity: 0.5, access_count: 0, source_type: "memory" as const,
      forget_kind: "artifact" as const,
    })
    const { rerender } = render(<KnowledgeConsole {...baseProps({ hasQueried: true, memorySources: [mem("m1")] })} />, { wrapper })
    fireEvent.click(screen.getByRole("button", { name: "Select" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Memory: m1" }))
    expect(screen.getByRole("button", { name: "Forget selected (1)" })).toBeInTheDocument()
    rerender(<KnowledgeConsole {...baseProps({ hasQueried: true, memorySources: [mem("m2")] })} />)
    expect(screen.getByRole("button", { name: "Forget selected (0)" })).toBeDisabled()
  })
})
