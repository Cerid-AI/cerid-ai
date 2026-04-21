// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import type { SystemCheckResponse } from "@/lib/types"

const mockFetchSystemCheck = vi.fn<() => Promise<SystemCheckResponse>>()

vi.mock("@/lib/api", () => ({
  fetchSystemCheck: (...args: unknown[]) => mockFetchSystemCheck(...(args as [])),
}))

import { SystemCheckCard } from "@/components/setup/system-check-card"

const HEALTHY_RESULT: SystemCheckResponse = {
  ram_gb: 16,
  os: "macOS 26.4",
  cpu: "Apple M2 Max",
  cpu_cores: 12,
  gpu: "Apple M2 Max",
  gpu_acceleration: "metal",
  docker_running: true,
  env_exists: true,
  env_keys_present: ["OPENROUTER_API_KEY"],
  ollama_detected: true,
  ollama_url: "http://localhost:11434",
  ollama_models: ["llama3.2:3b"],
  lightweight_recommended: false,
  archive_path_exists: true,
  default_archive_path: "~/cerid-archive",
}

beforeEach(() => {
  vi.restoreAllMocks()
  mockFetchSystemCheck.mockResolvedValue(HEALTHY_RESULT)
})

describe("SystemCheckCard", () => {
  it("shows System Check heading", () => {
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    expect(screen.getByText("System Check")).toBeInTheDocument()
  })

  it("shows all 4 check items", () => {
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    expect(screen.getByText("System Memory")).toBeInTheDocument()
    expect(screen.getByText("Docker")).toBeInTheDocument()
    expect(screen.getByText("Configuration")).toBeInTheDocument()
    expect(screen.getByText("Ollama")).toBeInTheDocument()
  })

  it("shows 'Detecting...' while loading", () => {
    // Make the fetch never resolve during this test
    mockFetchSystemCheck.mockReturnValue(new Promise(() => {}))
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    const detectingElements = screen.getAllByText("Detecting...")
    expect(detectingElements).toHaveLength(4)
  })

  it("calls onCheckComplete with result after fetch resolves", async () => {
    const onCheckComplete = vi.fn()
    render(<SystemCheckCard onCheckComplete={onCheckComplete} />)
    await waitFor(() => {
      expect(onCheckComplete).toHaveBeenCalledWith(HEALTHY_RESULT)
    })
  })

  it("shows resolved check details after fetch succeeds", async () => {
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText(/16 GB/)).toBeInTheDocument()
    })
    expect(screen.getByText("Running")).toBeInTheDocument()
    expect(screen.getByText(/1 key configured/)).toBeInTheDocument()
    expect(screen.getByText(/Detected \(1 model\)/)).toBeInTheDocument()
  })

  it("shows error message when fetch fails", async () => {
    mockFetchSystemCheck.mockRejectedValue(new Error("Network error"))
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText("Could not reach backend — is Docker running?")).toBeInTheDocument()
    })
  })
})
