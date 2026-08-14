// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const mockFetchRegistry = vi.fn()
const mockFetchInstalled = vi.fn()

vi.mock("@/lib/api", () => ({
  fetchKnowledgePackRegistry: (...args: unknown[]) => mockFetchRegistry(...args),
  fetchInstalledKnowledgePacks: (...args: unknown[]) => mockFetchInstalled(...args),
  installKnowledgePack: vi.fn(),
  uninstallKnowledgePack: vi.fn(),
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
