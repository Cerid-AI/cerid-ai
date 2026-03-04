// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import SettingsPane from "@/components/settings/settings-pane"

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
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SettingsPane", () => {
  it("shows loading state initially", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {}))) // never resolves
    render(<SettingsPane />)
    // Component shows "Loading settings..." with Loader2 spinner
    expect(screen.getByText(/loading settings/i)).toBeInTheDocument()
  })

  it("renders settings after loading", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />)
    expect(await screen.findByText("0.8.0")).toBeInTheDocument()
  })

  it("shows version number", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />)
    expect(await screen.findByText("0.8.0")).toBeInTheDocument()
  })

  it("shows machine ID", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />)
    expect(await screen.findByText("test-machine")).toBeInTheDocument()
  })

  it("shows feature tier badge", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />)
    expect(await screen.findByText("community")).toBeInTheDocument()
  })

  it("shows storage mode in select", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />)
    await screen.findByText("0.8.0")
    // Storage mode is shown via a Select component
    expect(screen.getByText(/Extract Only/i)).toBeInTheDocument()
  })

  it("displays collapsible section headings", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />)
    await screen.findByText("0.8.0")
    // Section headings are rendered as buttons with text
    expect(screen.getByText("Connection")).toBeInTheDocument()
    expect(screen.getByText("Ingestion")).toBeInTheDocument()
    expect(screen.getByText("Features")).toBeInTheDocument()
  })

  it("shows error state when fetch fails", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "Server error" }, 500))
    render(<SettingsPane />)
    await waitFor(() => {
      expect(screen.getByText(/failed/i)).toBeInTheDocument()
    })
  })

  it("shows hallucination check toggle", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />)
    await screen.findByText("0.8.0")
    expect(screen.getByText(/Hallucination Check/i)).toBeInTheDocument()
  })

  it("shows domains in taxonomy section", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSettings))
    render(<SettingsPane />)
    await screen.findByText("0.8.0")
    expect(screen.getByText("coding")).toBeInTheDocument()
  })
})
