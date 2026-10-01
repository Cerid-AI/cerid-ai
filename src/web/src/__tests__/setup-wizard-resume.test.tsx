// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Resuming the wizard restores every choice it saved, checked against what
 * the server reports now, so Apply sends what the user chose before leaving.
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
  ollama_configured_model: null,
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

const STATUS = {
  configured: false,
  setup_required: false,
  missing_keys: [],
  optional_keys: [],
  configured_providers: ["openrouter"],
}

vi.mock("@/lib/api", () => ({
  applySetupConfig: vi.fn(),
  validateProviderKey: vi.fn(),
  fetchSetupStatus: vi.fn(),
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
import { fetchSetupStatus, fetchSystemCheck } from "@/lib/api"
import { applySetupConfiguration } from "@/lib/api/setup"

const STORAGE_KEY = "cerid-setup-progress"

const SAVED = {
  version: 5,
  step: 4,
  skippedSteps: [] as unknown[],
  kbConfig: {
    archivePath: "/Volumes/library/kb",
    domains: ["general", "code"],
    lightweightMode: true,
    watchFolder: true,
  },
  ollama: { detected: true, enabled: true, model: "qwen3.5-4b-instruct", pulling: false },
  selectedMode: "simple" as string,
  selectedBackend: "cloud" as string | null,
  applied: false,
}

function seed(overrides: Partial<typeof SAVED> = {}) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...SAVED, ...overrides, ts: Date.now() }))
}

async function resume() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <SetupWizard open={true} onComplete={() => {}} />
    </QueryClientProvider>,
  )
  fireEvent.click(await screen.findByRole("button", { name: /^resume$/i }))
}

async function resumeAndApply() {
  await resume()
  await screen.findByText(/Review & Apply/i)
  const apply = screen.getByRole("button", { name: /apply configuration/i })
  await waitFor(() => expect(apply).toBeEnabled())
  fireEvent.click(apply)
  await waitFor(() => expect(applySetupConfiguration).toHaveBeenCalled())
  return vi.mocked(applySetupConfiguration).mock.calls[0][0]
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(fetchSetupStatus).mockResolvedValue(STATUS)
  vi.mocked(fetchSystemCheck).mockResolvedValue(SYSTEM_CHECK)
  vi.mocked(applySetupConfiguration).mockResolvedValue({ success: true })
})

describe("SetupWizard — resume restores what was saved", () => {
  it("applies the saved backend, storage settings and local model", async () => {
    seed()
    const sent = await resumeAndApply()
    expect(sent.inference_backend).toBe("cloud")
    expect(sent.archive_path).toBe("/Volumes/library/kb")
    expect(sent.domains).toEqual(["general", "code"])
    expect(sent.lightweight_mode).toBe(true)
    expect(sent.watch_folder).toBe(true)
    expect(sent.ollama_enabled).toBe(true)
    expect(sent.ollama_model).toBe("qwen3.5-4b-instruct")
  })

  it("applies the recommended backend when none had been clicked", async () => {
    seed({ selectedBackend: null })
    const sent = await resumeAndApply()
    expect(sent.inference_backend).toBe("ollama")
  })

  it("still applies the saved backend when the system check fails", async () => {
    vi.mocked(fetchSystemCheck).mockRejectedValue(new Error("unreachable"))
    seed()
    const sent = await resumeAndApply()
    expect(sent.inference_backend).toBe("cloud")
    expect(sent.domains).toEqual(["general", "code"])
  })

  it("goes back over a step that was skipped before leaving", async () => {
    seed({ skippedSteps: [3] })
    await resume()
    await screen.findByText(/Review & Apply/i)
    fireEvent.click(screen.getByRole("button", { name: /back/i }))
    expect(await screen.findByText(/Storage & Archive/i)).toBeInTheDocument()
  })

  it("shows the configuration as applied when it had been", async () => {
    seed({ applied: true })
    await resume()
    expect(await screen.findByText("Configuration applied successfully")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /apply configuration/i })).not.toBeInTheDocument()
  })

  it("opens in the saved mode", async () => {
    seed({ step: 8, selectedMode: "advanced", applied: true })
    await resume()
    fireEvent.click(await screen.findByRole("button", { name: /open cerid ai/i }))
    expect(localStorage.getItem("cerid-settings-mode")).toBe("advanced")
  })
})

describe("SetupWizard — resume drops what is no longer valid", () => {
  it("drops a model the server no longer serves", async () => {
    seed({ ollama: { ...SAVED.ollama, model: "llama3.1-8b" } })
    const sent = await resumeAndApply()
    expect(sent.ollama_model).toBeUndefined()
  })

  it("turns local inference off when no local server answers", async () => {
    vi.mocked(fetchSystemCheck).mockResolvedValue({
      ...SYSTEM_CHECK,
      ollama_detected: false,
      ollama_models: [],
    })
    seed()
    const sent = await resumeAndApply()
    expect(sent.ollama_enabled).toBe(false)
    expect(sent.ollama_model).toBeUndefined()
  })

  it("ignores a backend, mode and skipped steps this build does not know", async () => {
    seed({ selectedBackend: "vllm", selectedMode: "expert", skippedSteps: [99, "3", 4] })
    const sent = await resumeAndApply()
    expect(sent.inference_backend).toBe("ollama")
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).skippedSteps).toEqual([])
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).selectedMode).toBe("simple")
  })

  it("keeps a storage setting of the wrong type at its default", async () => {
    seed({
      kbConfig: {
        archivePath: 7,
        domains: "general",
        lightweightMode: "yes",
        watchFolder: null,
      } as unknown as typeof SAVED.kbConfig,
    })
    const sent = await resumeAndApply()
    expect(sent.archive_path).toBe("~/cerid-archive")
    expect(sent.domains).toEqual(["general"])
    expect(sent.lightweight_mode).toBe(false)
    expect(sent.watch_folder).toBe(false)
  })
})

describe("SetupWizard — saved progress", () => {
  it("holds no provider keys", async () => {
    seed()
    await resumeAndApply()
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
    expect(Object.keys(saved).sort()).toEqual([
      "applied", "kbConfig", "ollama", "selectedBackend", "selectedMode",
      "skippedSteps", "step", "ts", "version",
    ])
    expect(JSON.stringify(saved)).not.toMatch(/from \.env|openrouter/i)
  })
})
