// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const mockFetchRegistry = vi.fn()
const mockFetchInstalled = vi.fn()
const mockInstall = vi.fn()

vi.mock("@/lib/api", () => ({
  fetchKnowledgePackRegistry: (...args: unknown[]) => mockFetchRegistry(...args),
  fetchInstalledKnowledgePacks: (...args: unknown[]) => mockFetchInstalled(...args),
  installKnowledgePack: (...args: unknown[]) => mockInstall(...args),
  uninstallKnowledgePack: vi.fn(),
}))

vi.mock("@/lib/api/knowledge-packs", () => ({
  fetchKnowledgePackRegistry: (...args: unknown[]) => mockFetchRegistry(...args),
}))

vi.mock("@/lib/api/setup", () => ({
  startPackInstall: (...args: unknown[]) => mockInstall(...args),
}))

import { KnowledgeLibraryDialog } from "@/components/kb/knowledge-library-dialog"

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// WB-14: an errored registry/installed fetch must not render the same
// "intentionally empty" / "no packs installed" copy as a genuinely empty
// response — a failed request told users to go set an env var that was
// already set, or reinstall packs that were already installed.
describe("KnowledgeLibraryDialog", () => {
  it("shows the genuine empty-registry message when the registry loads with no packs", async () => {
    mockFetchRegistry.mockResolvedValue({ schema_version: 1, packs_by_domain: {} })
    mockFetchInstalled.mockResolvedValue({ schema_version: 1, packs: [] })
    render(<KnowledgeLibraryDialog open onOpenChange={vi.fn()} />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/intentionally empty/i)).toBeInTheDocument()
    })
  })

  it("shows a genuine error state, not 'intentionally empty', when the registry fetch fails", async () => {
    mockFetchRegistry.mockRejectedValue(new Error("network error"))
    mockFetchInstalled.mockResolvedValue({ schema_version: 1, packs: [] })
    render(<KnowledgeLibraryDialog open onOpenChange={vi.fn()} />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/couldn't load the knowledge pack registry/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/intentionally empty/i)).not.toBeInTheDocument()
  })

  it("shows a genuine error state, not 'no knowledge packs installed', when the installed fetch fails", async () => {
    const user = userEvent.setup()
    mockFetchRegistry.mockResolvedValue({ schema_version: 1, packs_by_domain: {} })
    mockFetchInstalled.mockRejectedValue(new Error("network error"))
    render(<KnowledgeLibraryDialog open onOpenChange={vi.fn()} />, { wrapper: createWrapper() })
    await user.click(screen.getByRole("tab", { name: /installed/i }))
    await waitFor(() => {
      expect(screen.getByText(/couldn't load installed packs/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/no knowledge packs installed/i)).not.toBeInTheDocument()
  })
})

// F220: POST /knowledge_packs/{id}/install answers 202 {job_id, status:
// "queued"} — the install runs as a background processor job and the caller
// must poll the registry's installing/installed flags. Treating the 202 as a
// finished install told the user the pack was ready before ingestion started.
describe("KnowledgeLibraryDialog — queued install", () => {
  const pack = {
    id: "pack-a",
    name: "Pack A",
    version: "1.0.0",
    description: "",
    domain: "general",
    sub_category: "",
    tags: [],
    license: "",
    size_bytes: 0,
    artifact_count: 0,
    download_url: "",
    sha256: "",
    provenance: {},
  }
  const registry = (flags: { installed: boolean; installing: boolean }) => ({
    schema_version: 1,
    packs_by_domain: { general: [{ ...pack, ...flags }] },
  })

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("keeps the pack in an installing state until the registry reports it installed", async () => {
    const onPackInstalled = vi.fn()
    mockFetchRegistry.mockResolvedValue(registry({ installed: false, installing: false }))
    mockFetchInstalled.mockResolvedValue({ schema_version: 1, packs: [] })
    mockInstall.mockResolvedValue({ job_id: "job-1", status: "queued" })

    render(
      <KnowledgeLibraryDialog open onOpenChange={vi.fn()} onPackInstalled={onPackInstalled} />,
      { wrapper: createWrapper() },
    )
    await vi.advanceTimersByTimeAsync(0)

    fireEvent.click(screen.getByRole("button", { name: /install/i }))
    await vi.advanceTimersByTimeAsync(0)

    // The job is queued, not done: no completion callback yet, and the card
    // must say so rather than falling back to a resting "Install" button.
    expect(onPackInstalled).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: /installing/i })).toBeInTheDocument()

    mockFetchRegistry.mockResolvedValue(registry({ installed: true, installing: false }))
    await vi.advanceTimersByTimeAsync(2500)

    expect(onPackInstalled).toHaveBeenCalledWith("pack-a")
  })

  it("surfaces a failed background install instead of reporting success", async () => {
    const onPackInstalled = vi.fn()
    mockFetchRegistry.mockResolvedValue(registry({ installed: false, installing: true }))
    mockFetchInstalled.mockResolvedValue({ schema_version: 1, packs: [] })
    mockInstall.mockResolvedValue({ job_id: "job-1", status: "queued" })

    render(
      <KnowledgeLibraryDialog open onOpenChange={vi.fn()} onPackInstalled={onPackInstalled} />,
      { wrapper: createWrapper() },
    )
    await vi.advanceTimersByTimeAsync(0)
    fireEvent.click(screen.getByRole("button", { name: /install/i }))
    await vi.advanceTimersByTimeAsync(2500)

    // Job observed running, then the flags settle with installed still false.
    mockFetchRegistry.mockResolvedValue(registry({ installed: false, installing: false }))
    await vi.advanceTimersByTimeAsync(2500)
    await act(async () => {})

    expect(onPackInstalled).not.toHaveBeenCalled()
    expect(screen.getByText(/pack install failed/i)).toBeInTheDocument()
  })
})
