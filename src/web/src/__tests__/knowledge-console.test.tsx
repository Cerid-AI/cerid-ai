// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
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
})
