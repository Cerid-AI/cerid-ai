// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * The setup wizard names the detected local model server from the system
 * check's `local_server_name`. "ollama" stays the backend id it writes.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { SystemCheckResponse } from "@/lib/types"

const mockFetchSystemCheck = vi.fn<() => Promise<SystemCheckResponse>>()

vi.mock("@/lib/api", () => ({
  fetchSystemCheck: (...args: unknown[]) => mockFetchSystemCheck(...(args as [])),
  fetchModelDoctor: vi.fn().mockResolvedValue({
    hardware_profile: "metal",
    ok: true,
    findings: [],
    known_good_local: {},
    candidate_upgrades: { chat: [], embed: [], rerank: [] },
    catalog_size: 0,
  }),
}))

import { SystemCheckCard } from "@/components/setup/system-check-card"
import { BackendRecommendationStep } from "@/components/setup/backend-recommendation-step"
import { ModeSelectionStep } from "@/components/setup/mode-selection-step"
import { backendOptionsForHardware } from "@/lib/hardware-profile"

function check(overrides: Partial<SystemCheckResponse> = {}): SystemCheckResponse {
  return {
    ram_gb: 64,
    os: "macOS 26",
    cpu: "Apple M4 Max",
    cpu_cores: 16,
    gpu: "Apple M4 Max",
    gpu_acceleration: "metal",
    docker_running: true,
    env_exists: true,
    env_keys_present: [],
    ollama_detected: true,
    ollama_url: "http://host.docker.internal:11434",
    ollama_models: ["qwen3.5-4b-instruct", "gemma-4-26b-a4b"],
    lightweight_recommended: false,
    archive_path_exists: true,
    default_archive_path: "~/cerid-archive",
    recommended_local_backend: "ollama",
    local_server_name: "MLX server",
    local_server_version: "cerid-mlx-c33d6c31d1f1",
    ...overrides,
  }
}

beforeEach(() => {
  mockFetchSystemCheck.mockReset()
})

describe("system check card", () => {
  it("names the detected server", async () => {
    mockFetchSystemCheck.mockResolvedValue(check())
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await waitFor(() => expect(screen.getByText("MLX server")).toBeInTheDocument())
    expect(screen.getByText(/Detected \(2 models\)/)).toBeInTheDocument()
    expect(screen.queryByText("Ollama")).not.toBeInTheDocument()
  })

  it("uses the neutral name when nothing is detected", async () => {
    mockFetchSystemCheck.mockResolvedValue(
      check({ ollama_detected: false, ollama_models: [], local_server_name: "Local model server" }),
    )
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await waitFor(() => expect(screen.getByText("Not found")).toBeInTheDocument())
    expect(screen.getByText("Local model server")).toBeInTheDocument()
    expect(screen.queryByText("Ollama")).not.toBeInTheDocument()
  })
})

describe("backend options", () => {
  it("offer the detected server under its own name, with the same id", () => {
    const { options, defaultId } = backendOptionsForHardware(check())
    expect(defaultId).toBe("ollama")
    const local = options.find((o) => o.id === "ollama")
    expect(local?.label).toBe("MLX server (Local)")
    expect(local?.blurb).not.toMatch(/Ollama/)
  })

  it("keep Ollama's name when Ollama is what answers", () => {
    const { options } = backendOptionsForHardware(check({ local_server_name: "Ollama" }))
    expect(options.find((o) => o.id === "ollama")?.label).toBe("Ollama (Local)")
  })

  it("keep Ollama's name when no server is detected", () => {
    const { options } = backendOptionsForHardware(
      check({ ollama_detected: false, local_server_name: "Local model server" }),
    )
    expect(options.find((o) => o.id === "ollama")?.label).toBe("Ollama (Local)")
  })

  it("are rendered with the detected server's name", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <BackendRecommendationStep systemCheck={check()} selected={null} onSelect={vi.fn()} />
      </QueryClientProvider>,
    )
    expect(screen.getByText("MLX server (Local)")).toBeInTheDocument()
    expect(screen.queryByText(/Ollama \(Local\)/)).not.toBeInTheDocument()
  })
})

describe("mode summary", () => {
  it("names the backend by the detected server", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={vi.fn()}
        configSummary={{
          providerCount: 0,
          providerNames: [],
          domainCount: 4,
          ollamaEnabled: true,
          ollamaModel: "gemma-4-26b-a4b",
          documentCount: 0,
          inferenceBackend: "ollama",
          localServerName: "MLX server",
        }}
      />,
    )
    expect(screen.getByText(/Backend: MLX server/)).toBeInTheDocument()
    expect(screen.queryByText(/Backend: Ollama/)).not.toBeInTheDocument()
  })
})
