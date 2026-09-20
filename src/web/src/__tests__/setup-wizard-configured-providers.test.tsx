// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

vi.mock("@/lib/api", () => ({
  applySetupConfig: vi.fn(),
  validateProviderKey: vi.fn(),
  fetchSetupStatus: vi.fn(),
  fetchSetupHealth: vi.fn().mockResolvedValue({ services: [] }),
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

vi.mock("@/lib/log-swallowed", () => ({ logSwallowedError: vi.fn() }))

const dashboardProps: Array<Record<string, unknown>> = []
vi.mock("@/components/setup/health-dashboard", () => ({
  HealthDashboard: (props: Record<string, unknown>) => {
    dashboardProps.push(props)
    return <div data-testid="health-dashboard" />
  },
}))

import { SetupWizard } from "@/components/setup/setup-wizard"
import { configuredProviderIds } from "@/components/setup/configured-providers"
import { fetchSetupStatus } from "@/lib/api"

const STORAGE_KEY = "cerid-setup-progress"

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  dashboardProps.length = 0
})

describe("configuredProviderIds", () => {
  const noKeys = {
    openrouter: { valid: false },
    openai: { valid: false },
    anthropic: { valid: false },
    xai: { valid: false },
  }
  const localOff = { enabled: false }

  it("lists every provider whose key validated", () => {
    expect(
      configuredProviderIds(
        { ...noKeys, openrouter: { valid: true }, anthropic: { valid: true } },
        localOff,
        null,
      ),
    ).toEqual(["openrouter", "anthropic"])
  })

  it("is empty when nothing is configured", () => {
    expect(configuredProviderIds(noKeys, localOff, null)).toEqual([])
  })

  it("adds 'ollama' when local inference is enabled", () => {
    expect(
      configuredProviderIds(noKeys, { enabled: true }, "ollama"),
    ).toEqual(["ollama"])
  })

  it("adds 'quenchforge' when the selected local backend is quenchforge", () => {
    expect(
      configuredProviderIds(noKeys, { enabled: true }, "quenchforge"),
    ).toEqual(["quenchforge"])
  })
})

describe("SetupWizard — Service Health step", () => {
  it("tells the health dashboard which providers setup configured", async () => {
    vi.mocked(fetchSetupStatus).mockResolvedValue({
      configured: true,
      setup_required: false,
      missing_keys: [],
      optional_keys: [],
      configured_providers: ["openrouter"],
    })
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: 5,
        step: 5,
        skippedSteps: [],
        kbConfig: { archivePath: "~/cerid-archive", domains: ["general"], lightweightMode: false, watchFolder: false },
        ollama: { detected: false, enabled: false, model: null, pulling: false },
        selectedMode: "simple",
        selectedBackend: null,
        applied: true,
        ts: Date.now(),
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <SetupWizard open={true} onComplete={() => {}} />
      </QueryClientProvider>,
    )
    fireEvent.click(await screen.findByRole("button", { name: /^resume$/i }))
    await screen.findByTestId("health-dashboard")
    const last = dashboardProps[dashboardProps.length - 1]
    expect(last.configuredProviders).toEqual(["openrouter"])
  })
})
