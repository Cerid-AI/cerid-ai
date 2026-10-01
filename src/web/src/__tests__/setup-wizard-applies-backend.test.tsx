// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * The backend shown as selected on the first step and the model shown on the
 * review step are what Apply sends, so they are what inference uses.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const SYSTEM_CHECK = {
  ram_gb: 64,
  docker_running: true,
  env_exists: true,
  env_keys_present: [],
  ollama_detected: true,
  ollama_url: "http://host.docker.internal:11434",
  ollama_models: ["qwen3.5-4b-instruct", "gemma-4-26b-a4b"],
  ollama_configured_model: "gemma-4-26b-a4b",
  local_server_name: "MLX server",
  lightweight_recommended: false,
  archive_path_exists: false,
  default_archive_path: "~/cerid-archive",
  os: "darwin",
  cpu: "Apple M4 Max",
  cpu_cores: 16,
  gpu: "Apple M4 Max",
  gpu_acceleration: "metal",
  recommended_local_backend: "ollama" as const,
}

vi.mock("@/lib/api", () => ({
  applySetupConfig: vi.fn(),
  validateProviderKey: vi.fn(),
  fetchSetupStatus: vi.fn().mockResolvedValue({
    configured: false,
    setup_required: true,
    missing_keys: ["OPENROUTER_API_KEY"],
    optional_keys: [],
    configured_providers: [],
  }),
  fetchSetupHealth: vi.fn().mockResolvedValue({ services: [] }),
  fetchProviderCredits: vi.fn().mockResolvedValue({ configured: false, balance: null }),
  fetchSystemCheck: vi.fn(),
  fetchModelDoctor: vi.fn().mockResolvedValue({
    hardware_profile: "metal",
    ok: true,
    findings: [],
    known_good_local: {},
    candidate_upgrades: {},
    catalog_size: 0,
  }),
  fetchOllamaRecommendations: vi.fn().mockResolvedValue({ hardware: null, models: [] }),
  uploadFile: vi.fn(),
  queryKB: vi.fn(),
  pullOllamaModel: vi.fn(),
  MCP_BASE: "http://localhost:8888",
  mcpHeaders: vi.fn().mockReturnValue({}),
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
import { fetchSystemCheck } from "@/lib/api"
import { applySetupConfiguration } from "@/lib/api/setup"

function renderWizard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SetupWizard open={true} onComplete={() => {}} />
    </QueryClientProvider>,
  )
}

async function applyFromFirstStep(pick?: RegExp) {
  renderWizard()
  await screen.findByTestId("model-compat-compact")
  if (pick) fireEvent.click(screen.getByRole("button", { name: pick }))
  fireEvent.click(screen.getByRole("button", { name: /get started/i }))
  fireEvent.click(await screen.findByRole("button", { name: /next/i }))
  fireEvent.click(screen.getByRole("button", { name: /next/i }))
  fireEvent.click(screen.getByRole("button", { name: /next/i }))
  await screen.findByText(/Review & Apply/i)
  fireEvent.click(screen.getByRole("button", { name: /apply configuration/i }))
  await waitFor(() => expect(applySetupConfiguration).toHaveBeenCalled())
  return vi.mocked(applySetupConfiguration).mock.calls[0][0]
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(fetchSystemCheck).mockResolvedValue(SYSTEM_CHECK)
  vi.mocked(applySetupConfiguration).mockResolvedValue({ success: true })
})

describe("SetupWizard — Apply sends the backend", () => {
  it("sends the backend shown as selected when the user did not click one", async () => {
    const sent = await applyFromFirstStep()
    expect(sent.inference_backend).toBe("ollama")
    expect(sent.ollama_model).toBe("gemma-4-26b-a4b")
    expect(sent.ollama_enabled).toBe(true)
  })

  it("sends cloud when the user picks cloud", async () => {
    const sent = await applyFromFirstStep(/Cloud \(OpenRouter\)/)
    expect(sent.inference_backend).toBe("cloud")
  })
})

describe("SetupWizard — Apply refused", () => {
  it("shows the reason the backend gave", async () => {
    vi.mocked(applySetupConfiguration).mockResolvedValue({
      success: false,
      error: "Choose a local model before enabling the local backend.",
    })
    await applyFromFirstStep()
    expect(
      await screen.findByText("Choose a local model before enabling the local backend."),
    ).toBeInTheDocument()
  })
})
