// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SetupWizard Apply guard (beta triage 2026-07-12 P0-B4).
 *
 * Re-running the wizard on an already-configured backend must not silently
 * rewrite env config: Apply first shows an explicit overwrite confirmation,
 * only then sends force=true, and a backend 409 renders its message instead
 * of the generic connection error. Uses the wizard's own resume mechanism
 * (persisted-progress record + "Resume") to reach step 4 — same approach as
 * setup-wizard-full.test.tsx.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

vi.mock("@/lib/api", () => ({
  applySetupConfig: vi.fn(),
  validateProviderKey: vi.fn(),
  fetchSetupStatus: vi.fn().mockResolvedValue({
    configured: true,
    setup_required: false,
    missing_keys: [],
    optional_keys: ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"],
    configured_providers: [],
  }),
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
import { applySetupConfiguration, completeOnboarding } from "@/lib/api/setup"

const noop = () => {}

function renderWizard(props: Partial<React.ComponentProps<typeof SetupWizard>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SetupWizard open={true} onComplete={noop} {...props} />
    </QueryClientProvider>,
  )
}

/** Seed the wizard's persisted-progress record at step 4 (Review & Apply)
 *  with a local backend enabled so the Apply button is not key-gated. */
function seedResumeAtApplyStep() {
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
}

async function renderAtApplyStep() {
  seedResumeAtApplyStep()
  renderWizard()
  fireEvent.click(await screen.findByRole("button", { name: /resume/i }))
  await screen.findByText(/Review & Apply/i)
  // Let the mount-time setup-status fetches settle so backendConfigured is set.
  await waitFor(() => expect(fetchSetupStatus).toHaveBeenCalled())
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(fetchSetupStatus).mockResolvedValue({
    configured: true,
    setup_required: false,
    missing_keys: [],
    optional_keys: ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"],
    configured_providers: [],
  })
  vi.mocked(applySetupConfiguration).mockResolvedValue({ success: true })
  vi.mocked(completeOnboarding).mockResolvedValue({ onboarding_complete: true })
})

describe("SetupWizard — already-configured Apply guard", () => {
  it("shows the overwrite confirmation instead of applying when the backend is configured", async () => {
    await renderAtApplyStep()

    fireEvent.click(screen.getByRole("button", { name: /apply configuration/i }))

    expect(
      await screen.findByText(/already configured — overwrite settings\?/i),
    ).toBeInTheDocument()
    expect(applySetupConfiguration).not.toHaveBeenCalled()
  })

  it("Cancel dismisses the confirmation without applying", async () => {
    await renderAtApplyStep()
    fireEvent.click(screen.getByRole("button", { name: /apply configuration/i }))
    await screen.findByText(/already configured — overwrite settings\?/i)

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }))

    expect(screen.queryByText(/already configured — overwrite settings\?/i)).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /apply configuration/i })).toBeInTheDocument()
    expect(applySetupConfiguration).not.toHaveBeenCalled()
  })

  it("Overwrite sends force=true", async () => {
    await renderAtApplyStep()
    fireEvent.click(screen.getByRole("button", { name: /apply configuration/i }))
    await screen.findByText(/already configured — overwrite settings\?/i)

    fireEvent.click(screen.getByRole("button", { name: /overwrite settings/i }))

    await waitFor(() =>
      expect(applySetupConfiguration).toHaveBeenCalledWith(
        expect.objectContaining({ archive_path: "~/cerid-archive" }),
        { force: true },
      ),
    )
  })

  it("surfaces the backend 409 message gracefully", async () => {
    vi.mocked(applySetupConfiguration).mockResolvedValue({
      success: false,
      conflict: true,
      error: "Cerid is already configured — pass force=true to reconfigure and overwrite the existing settings.",
    })
    await renderAtApplyStep()
    fireEvent.click(screen.getByRole("button", { name: /apply configuration/i }))
    fireEvent.click(await screen.findByRole("button", { name: /overwrite settings/i }))

    expect(
      await screen.findByText(/pass force=true to reconfigure/i),
    ).toBeInTheDocument()
  })

  it("applies directly (force=false) when the backend is not configured", async () => {
    // setup_required=true (not configured) — provider_status still marks an
    // openrouter key valid so the Apply button clears its key gate (resume
    // does not restore the persisted ollama/keys state).
    vi.mocked(fetchSetupStatus).mockResolvedValue({
      configured: false,
      setup_required: true,
      missing_keys: ["OPENROUTER_API_KEY"],
      optional_keys: ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"],
      configured_providers: [],
      provider_status: {
        openrouter: { configured: true, key_env_var: "OPENROUTER_API_KEY", key_present: true },
      },
    })
    await renderAtApplyStep()

    fireEvent.click(screen.getByRole("button", { name: /apply configuration/i }))

    await waitFor(() =>
      expect(applySetupConfiguration).toHaveBeenCalledWith(expect.anything(), { force: false }),
    )
    expect(screen.queryByText(/overwrite settings\?/i)).not.toBeInTheDocument()
  })
})

describe("SetupWizard — onboarding-complete on finish", () => {
  it("'Skip setup' posts the server-side onboarding flag and completes", async () => {
    const onComplete = vi.fn()
    renderWizard({ canSkip: true, onComplete })

    fireEvent.click(
      await screen.findByRole("button", { name: /skip setup — i've already configured cerid/i }),
    )

    expect(completeOnboarding).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem("cerid-onboarding-complete")).toBe("true")
  })
})
