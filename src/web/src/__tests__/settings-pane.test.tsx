// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
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
  feature_flags: { hallucination_check: true, feedback_loop: false },
  domains: ["coding", "finance", "projects", "personal", "general", "conversations"],
  taxonomy: {
    coding: { description: "Code artifacts", icon: "code", sub_categories: ["python", "javascript"] },
    finance: { description: "Financial docs", icon: "dollar", sub_categories: ["budgets", "taxes"] },
  },
  storage_mode: "extract_only",
  sync_backend: "local",
  machine_id: "test-machine",
  version: "0.8.0",
}

function mockFetch(data: unknown, status = 200) {
  return vi.fn().mockImplementation((url: string) => {
    // KB stats endpoint gets a default empty response
    if (typeof url === "string" && url.includes("/admin/kb/stats")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ total_artifacts: 0, total_chunks: 0, domains: {} }),
        text: () => Promise.resolve("{}"),
      })
    }
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(data),
      text: () => Promise.resolve(JSON.stringify(data)),
    })
  })
}

/** Click a Radix tab trigger by its name */
async function clickTab(name: string) {
  const user = userEvent.setup()
  await user.click(screen.getByRole("tab", { name }))
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe("SettingsPane", () => {
  it("shows loading state initially", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {}))) // never resolves
    render(<SettingsPane />, { wrapper })
    // Component shows "Loading settings..." with Loader2 spinner
    expect(screen.getByText(/loading settings/i)).toBeInTheDocument()
  })

  it("renders settings after loading", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    expect(await screen.findByText("Knowledge & Ingestion")).toBeInTheDocument()
  })

  it("shows version number on System tab", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    expect(await screen.findByText("0.8.0")).toBeInTheDocument()
  })

  it("shows machine ID on System tab", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    expect(await screen.findByText("test-machine")).toBeInTheDocument()
  })

  it("shows feature tier badge on System tab", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    expect(await screen.findByText("community")).toBeInTheDocument()
  })

  it("shows storage mode in select", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    // Storage mode is shown via a Select component on Essentials tab
    expect(screen.getByText(/Extract Only/i)).toBeInTheDocument()
  })

  it("displays Essentials tab section headings by default", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    // Essentials tab shows Knowledge & Ingestion and AI Features
    expect(screen.getByText("Knowledge & Ingestion")).toBeInTheDocument()
    expect(screen.getByText("AI Features")).toBeInTheDocument()
  })

  it("shows error state when fetch fails", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "Server error" }, 500))
    render(<SettingsPane />, { wrapper })
    await waitFor(() => {
      expect(screen.getByText(/server error/i)).toBeInTheDocument()
    })
  })

  it("shows hallucination check toggle", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    // Appears in AI Features toggle on Essentials tab
    expect(screen.getAllByText(/Hallucination Check/i).length).toBeGreaterThanOrEqual(1)
  })

  it("shows domains in taxonomy section on System tab", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    expect(await screen.findByText("coding")).toBeInTheDocument()
  })

  it("renders Switch components for feature toggles", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    // Switch components render as role=switch buttons
    const switches = screen.getAllByRole("switch")
    expect(switches.length).toBeGreaterThanOrEqual(1)
  })

  it("shows Retrieval Pipeline on Pipeline tab", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("Pipeline")
    expect(await screen.findByText("Retrieval Pipeline")).toBeInTheDocument()
  })

  it("shows Connection section on System tab", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    await clickTab("System")
    expect(await screen.findByText("Connection")).toBeInTheDocument()
    expect(await screen.findByText("Platform Capabilities")).toBeInTheDocument()
  })

  it("renders tab triggers for Essentials, Pipeline, and System", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    expect(screen.getByRole("tab", { name: "Essentials" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Pipeline" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "System" })).toBeInTheDocument()
  })

  it("renders preset buttons", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    // Emoji and label are in separate spans; check for labels only
    expect(screen.getByText("Quick")).toBeInTheDocument()
    expect(screen.getByText("Balanced")).toBeInTheDocument()
    expect(screen.getByText("Maximum")).toBeInTheDocument()
  })

  it("shows Self-RAG toggle in AI Features section", async () => {
    vi.stubGlobal("fetch", mockFetch({ ...mockSettings, enable_self_rag: true }))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("Knowledge & Ingestion")
    expect(screen.getByText("Self-RAG Validation")).toBeInTheDocument()
  })
})
