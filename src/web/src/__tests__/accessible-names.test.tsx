// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Every control a keyboard or screen-reader user can reach states what it is
// (audit 30). Names are computed the way a browser does, not read off
// attributes.

import { describe, it, expect, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"

vi.mock("@/lib/api/routing", () => ({
  fetchRoutingInfo: vi.fn().mockResolvedValue({ ollama_available: false, ollama_models: [] }),
}))
vi.mock("@/lib/api/settings", () => ({
  fetchModelCatalog: vi.fn().mockResolvedValue({ source: "unavailable", ids: [] }),
}))
vi.mock("@/lib/api/domains", () => ({
  fetchDomainCounts: vi.fn().mockResolvedValue({
    domains: [
      { name: "coding", artifact_count: 3 },
      { name: "finance", artifact_count: 2 },
    ],
  }),
}))

import AutomationDialog from "@/components/automations/automation-dialog"
import { ModelSelect } from "@/components/chat/model-select"
import { ModeSelectionStep } from "@/components/setup/mode-selection-step"
import { MODELS } from "@/lib/types"

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>
}

describe("setup mode cards", () => {
  it("are named and say which one is chosen", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={() => {}}
        configSummary={{ providerCount: 0, providerNames: [], domainCount: 0, ollamaEnabled: false, ollamaModel: null, documentCount: 0 }}
      />,
    )
    expect(screen.getByRole("button", { name: /Clean & Simple/, pressed: true })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Advanced/, pressed: false })).toBeInTheDocument()
  })
})

describe("chat model picker", () => {
  it("names the picker and each option", async () => {
    render(wrap(<ModelSelect value={MODELS[0].id} onChange={() => {}} />))
    await userEvent.click(screen.getByRole("combobox", { name: "Model" }))
    for (const m of MODELS) {
      expect(screen.getByRole("option", { name: new RegExp(m.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) })).toBeInTheDocument()
    }
  })
})

describe("New Automation dialog", () => {
  async function openDialog() {
    render(wrap(<AutomationDialog open onClose={() => {}} onSave={() => {}} />))
    return screen.findByRole("dialog")
  }

  it("names the schedule picker", async () => {
    const dialog = await openDialog()
    expect(within(dialog).getByRole("combobox", { name: "Schedule" })).toBeInTheDocument()
  })

  it("names the action choices as one group and says which is chosen", async () => {
    const dialog = await openDialog()
    const group = within(dialog).getByRole("group", { name: "Action Type" })
    expect(within(group).getByRole("button", { name: "Notify", pressed: true })).toBeInTheDocument()
    expect(within(group).getByRole("button", { name: "Digest", pressed: false })).toBeInTheDocument()
    expect(within(group).getByRole("button", { name: "Ingest", pressed: false })).toBeInTheDocument()
  })

  it("names the custom schedule field", async () => {
    render(
      wrap(
        <AutomationDialog
          open
          onClose={() => {}}
          onSave={() => {}}
          automation={{
            id: "a1", name: "n", description: "", prompt: "p", action: "notify", schedule: "30 8 * * 2",
            domains: ["coding"], enabled: true, run_count: 0, last_run_at: null, last_status: null,
            created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
          }}
        />,
      ),
    )
    expect(await screen.findByRole("textbox", { name: "Custom cron expression" })).toHaveValue("30 8 * * 2")
  })

  it("names the domain checkboxes and their group", async () => {
    const dialog = await openDialog()
    const group = within(dialog).getByRole("group", { name: "Domains" })
    expect(await within(group).findByRole("checkbox", { name: "coding" })).toBeInTheDocument()
    expect(within(group).getByRole("checkbox", { name: "finance" })).toBeInTheDocument()
  })

  it("names the close button", async () => {
    const dialog = await openDialog()
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument()
  })
})
