// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * SetupWizard pre-configured provider detection against the real
 * GET /setup/status contract.
 *
 * The server sends `configured_providers` — the providers whose key is
 * actually set — and `optional_keys`, which is the *static* list of every
 * optional key name the build knows about, not the missing ones. A user who
 * already has OPENAI_API_KEY in .env must be shown as ready, not re-prompted.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

vi.mock("@/lib/api", () => ({
  applySetupConfig: vi.fn(),
  validateProviderKey: vi.fn(),
  fetchSetupStatus: vi.fn(),
  fetchSetupHealth: vi.fn().mockResolvedValue({ services: {} }),
  fetchProviderCredits: vi.fn().mockResolvedValue({ configured: false, balance: null }),
  fetchSystemCheck: vi.fn().mockResolvedValue({
    ram_gb: 16,
    docker_running: true,
    env_exists: true,
    env_keys_present: [],
    ollama_detected: false,
    ollama_url: null,
    ollama_models: [],
    lightweight_recommended: false,
    archive_path_exists: false,
    default_archive_path: "~/cerid-archive",
  }),
  fetchModelDoctor: vi.fn().mockResolvedValue({
    hardware_profile: "unknown",
    ok: true,
    findings: [],
    known_good_local: {},
    candidate_upgrades: {},
    catalog_size: 0,
  }),
  uploadFile: vi.fn(),
  queryKB: vi.fn(),
  pullOllamaModel: vi.fn(),
}))

vi.mock("@/lib/api/setup", () => ({
  applySetupConfiguration: vi.fn().mockResolvedValue({ success: true }),
  completeOnboarding: vi.fn().mockResolvedValue({ onboarding_complete: true }),
  startPackInstall: vi.fn().mockResolvedValue({ status: "installed", jobId: null }),
}))

vi.mock("@/hooks/use-drag-drop", () => ({
  useDragDrop: () => ({ isDragOver: false, dragHandlers: {} }),
}))

import { SetupWizard } from "@/components/setup/setup-wizard"
import { fetchSetupStatus } from "@/lib/api"

/** Exactly what src/mcp/app/routers/setup.py::setup_status returns — no
 *  `provider_status`, and `optional_keys` is the static _OPTIONAL_KEYS list. */
function statusPayload(configuredProviders: string[]) {
  return {
    configured: true,
    setup_required: false,
    missing_keys: [],
    optional_keys: ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "HF_TOKEN"],
    services: {},
    configured_providers: configuredProviders,
    onboarding_complete: false,
  }
}

async function renderAtReviewStep() {
  localStorage.setItem(
    "cerid-setup-progress",
    JSON.stringify({
      version: 5,
      step: 4,
      skippedSteps: [],
      kbConfig: { archivePath: "~/cerid-archive", domains: ["general"], lightweightMode: false, watchFolder: false },
      ollama: { detected: true, enabled: true, model: "llama3.1-8b", pulling: false },
      selectedMode: "simple",
      selectedBackend: null,
      applied: false,
      ts: Date.now(),
    }),
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <SetupWizard open={true} onComplete={() => {}} />
    </QueryClientProvider>,
  )
  fireEvent.click(await screen.findByRole("button", { name: /resume/i }))
  await screen.findByText(/Review & Apply/i)
  await waitFor(() => expect(fetchSetupStatus).toHaveBeenCalled())
}

/** The Review step renders one row per provider: label + "Ready" or "Not configured". */
function providerRow(label: string): HTMLElement {
  const name = screen.getByText(label)
  const row = name.closest("div")
  if (!row) throw new Error(`no row for ${label}`)
  return row
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

describe("SetupWizard — pre-configured providers from /setup/status", () => {
  it("marks a provider listed in configured_providers as ready", async () => {
    vi.mocked(fetchSetupStatus).mockResolvedValue(statusPayload(["openrouter", "openai"]))
    await renderAtReviewStep()

    await waitFor(() =>
      expect(within(providerRow("OpenAI")).getByText(/ready/i)).toBeInTheDocument(),
    )
  })

  it("leaves a provider absent from configured_providers unconfigured", async () => {
    vi.mocked(fetchSetupStatus).mockResolvedValue(statusPayload(["openrouter", "openai"]))
    await renderAtReviewStep()

    await waitFor(() =>
      expect(within(providerRow("OpenRouter")).getByText(/ready/i)).toBeInTheDocument(),
    )
    expect(within(providerRow("Anthropic")).getByText(/not configured/i)).toBeInTheDocument()
    expect(within(providerRow("xAI")).getByText(/not configured/i)).toBeInTheDocument()
  })
})
