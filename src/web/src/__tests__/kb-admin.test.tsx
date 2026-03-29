// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import SettingsPane from "@/components/settings/settings-pane"
import { UIModeProvider } from "@/contexts/ui-mode-context"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return (
    <UIModeProvider>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </UIModeProvider>
  )
}

const mockSettings = {
  categorize_mode: "smart",
  chunk_max_tokens: 400,
  chunk_overlap: 0.2,
  cost_sensitivity: "medium",
  enable_encryption: false,
  enable_feedback_loop: false,
  enable_hallucination_check: true,
  enable_memory_extraction: false,
  enable_model_router: false,
  hallucination_threshold: 0.75,
  enable_auto_inject: false,
  auto_inject_threshold: 0.82,
  feature_tier: "community",
  feature_flags: {},
  domains: ["code", "finance"],
  taxonomy: {},
  storage_mode: "extract_only",
  sync_backend: "local",
  machine_id: "test-machine",
  version: "0.8.0",
}

const mockKBStats = {
  total_artifacts: 42,
  total_chunks: 150,
  domains: {
    code: { artifacts: 30, chunks: 100, avg_quality: 0.75, synopsis_candidates: 5 },
    finance: { artifacts: 12, chunks: 50, avg_quality: 0.60, synopsis_candidates: 3 },
  },
}

function mockMultiFetch(settingsData: unknown, kbStatsData: unknown) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/admin/kb/stats")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(kbStatsData),
        text: () => Promise.resolve(JSON.stringify(kbStatsData)),
      })
    }
    if (url.includes("/providers/ollama/status")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ enabled: false, url: "http://localhost:11434", reachable: false, models: [], default_model: "llama3.2:3b", default_model_installed: false }),
        text: () => Promise.resolve("{}"),
      })
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(settingsData),
      text: () => Promise.resolve(JSON.stringify(settingsData)),
    })
  })
}

/** Switch to a tab using userEvent (Radix needs pointer events) */
async function clickTab(name: string) {
  const user = userEvent.setup()
  await user.click(screen.getByRole("tab", { name }))
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe("KB Management Section", () => {
  it("renders KB Management section heading", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion") // wait for load
    await clickTab("System")
    expect(await screen.findByText("KB Management")).toBeInTheDocument()
  })

  it("displays total artifact and chunk counts", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    await screen.findByText("KB Management")
    expect(await screen.findByText("42")).toBeInTheDocument()
    expect(await screen.findByText("150")).toBeInTheDocument()
  })

  it("shows per-domain stats", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    await screen.findByText("KB Management")
    // Domain names
    expect(await screen.findByText("code")).toBeInTheDocument()
    expect(await screen.findByText("finance")).toBeInTheDocument()
    // Artifact/chunk counts in "X / Y" format
    expect(await screen.findByText("30 / 100")).toBeInTheDocument()
    expect(await screen.findByText("12 / 50")).toBeInTheDocument()
  })

  it("renders management action buttons", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    await screen.findByText("KB Management")
    expect(await screen.findByText("Rebuild Indexes")).toBeInTheDocument()
    expect(screen.getByText("Rescore All")).toBeInTheDocument()
    expect(screen.getByText("Regenerate Summaries")).toBeInTheDocument()
    expect(screen.getByText("Refresh Stats")).toBeInTheDocument()
  })

  it("calls rebuild endpoint on button click", async () => {
    const fetchMock = mockMultiFetch(mockSettings, mockKBStats)
    vi.stubGlobal("fetch", fetchMock)
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")

    const rebuildBtn = await screen.findByText("Rebuild Indexes")

    // Update mock to handle the rebuild POST
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/admin/kb/rebuild-index") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ domains_rebuilt: 5, message: "Rebuilt BM25 indexes for 5 domains" }),
        })
      }
      if (url.includes("/admin/kb/stats")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockKBStats),
        })
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockSettings),
      })
    })

    fireEvent.click(rebuildBtn)

    await waitFor(() => {
      expect(screen.getByText(/Rebuilt BM25 indexes for 5 domains/)).toBeInTheDocument()
    })
  })

  it("shows clear confirmation when trash icon is clicked", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    await screen.findByText("KB Management")

    // Find the trash icons (one per domain with artifacts)
    const trashButtons = await screen.findAllByTitle(/Clear/)
    expect(trashButtons.length).toBeGreaterThan(0)

    fireEvent.click(trashButtons[0])

    // Should show confirm/cancel buttons
    expect(await screen.findByText("Clear")).toBeInTheDocument()
    expect(screen.getByText("Cancel")).toBeInTheDocument()
  })

  it("cancels clear confirmation", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    await screen.findByText("KB Management")

    const trashButtons = await screen.findAllByTitle(/Clear/)
    fireEvent.click(trashButtons[0])

    const cancelBtn = await screen.findByText("Cancel")
    fireEvent.click(cancelBtn)

    // Confirm/cancel should disappear
    await waitFor(() => {
      expect(screen.queryByText("Cancel")).not.toBeInTheDocument()
    })
  })
})
