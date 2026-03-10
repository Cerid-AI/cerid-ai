// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import SettingsPane from "@/components/settings/settings-pane"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
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

beforeEach(() => {
  vi.restoreAllMocks()
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
    expect(await screen.findByText("0.8.0")).toBeInTheDocument()
  })

  it("shows version number", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    expect(await screen.findByText("0.8.0")).toBeInTheDocument()
  })

  it("shows machine ID", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    expect(await screen.findByText("test-machine")).toBeInTheDocument()
  })

  it("shows feature tier badge", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    expect(await screen.findByText("community")).toBeInTheDocument()
  })

  it("shows storage mode in select", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("0.8.0")
    // Storage mode is shown via a Select component
    expect(screen.getByText(/Extract Only/i)).toBeInTheDocument()
  })

  it("displays collapsible section headings", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("0.8.0")
    // Section headings are rendered as buttons with text
    expect(screen.getByText("Connection")).toBeInTheDocument()
    expect(screen.getByText("Ingestion")).toBeInTheDocument()
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
    await screen.findByText("0.8.0")
    // Appears in both AI Features toggle and capabilities grid
    expect(screen.getAllByText(/Hallucination Check/i).length).toBeGreaterThanOrEqual(1)
  })

  it("shows domains in taxonomy section", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("0.8.0")
    expect(screen.getByText("coding")).toBeInTheDocument()
  })

  it("renders Switch components for feature toggles", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("0.8.0")
    // Switch components render as role=switch buttons
    const switches = screen.getAllByRole("switch")
    expect(switches.length).toBeGreaterThanOrEqual(1)
  })

  it("shows Retrieval Pipeline section heading", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("0.8.0")
    expect(screen.getByText("Retrieval Pipeline")).toBeInTheDocument()
  })

  it("integrates feature flags into Connection section as capabilities", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("0.8.0")
    expect(screen.getByText("Platform Capabilities")).toBeInTheDocument()
    // "Hallucination Check" appears in both capabilities grid and AI Features toggle
    expect(screen.getAllByText("Hallucination Check").length).toBeGreaterThanOrEqual(1)
  })

  it("does not render standalone Feature Flags section", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("0.8.0")
    // "Feature Flags" section heading should not exist
    const headings = screen.getAllByRole("button").map(b => b.textContent)
    expect(headings).not.toContain(expect.stringContaining("Feature Flags"))
  })

  it("shows Self-RAG toggle in AI Features section", async () => {
    vi.stubGlobal("fetch", mockFetch({ ...mockSettings, enable_self_rag: true }))
    render(<SettingsPane />, { wrapper })
    await screen.findByText("0.8.0")
    expect(screen.getByText("Self-RAG Validation")).toBeInTheDocument()
  })
})
