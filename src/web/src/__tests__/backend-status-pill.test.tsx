// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * The status-strip backend pill labelled the *recommended* backend as if it
 * were the active one, so a quenchforge host with a cloud recommendation read
 * "Cloud". It now prefers the provider /health/status reports.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@/components/ui/tooltip"
import { BackendStatusPill } from "@/components/layout/backend-status-pill"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}

const systemCheckRecommendingCloud = {
  ram_gb: 32, docker_running: true, env_exists: true, env_keys_present: [],
  ollama_detected: false, ollama_url: null, ollama_models: [],
  lightweight_recommended: false, archive_path_exists: true,
  default_archive_path: "/data", os: "macOS", cpu: "Intel Xeon W",
  cpu_cores: 16, gpu: "Radeon Pro Vega II", gpu_acceleration: "none",
  recommended_local_backend: "cloud",
}

function stub(health: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const body = url.includes("system-check") ? systemCheckRecommendingCloud : health
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      })
    }),
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("BackendStatusPill", () => {
  it("labels the configured quenchforge backend, not the recommendation", async () => {
    stub({ status: "healthy", services: {}, internal_llm_provider: "quenchforge" })
    render(<BackendStatusPill />, { wrapper })
    expect(await screen.findByText("Quenchforge")).toBeInTheDocument()
    expect(screen.queryByText("Cloud")).not.toBeInTheDocument()
  })

  it("labels a configured ollama backend Ollama", async () => {
    stub({ status: "healthy", services: {}, internal_llm_provider: "ollama" })
    render(<BackendStatusPill />, { wrapper })
    expect(await screen.findByText("Ollama")).toBeInTheDocument()
  })

  it("labels a configured openrouter backend Cloud", async () => {
    stub({ status: "healthy", services: {}, internal_llm_provider: "openrouter" })
    render(<BackendStatusPill />, { wrapper })
    expect(await screen.findByText("Cloud")).toBeInTheDocument()
  })

  it("falls back to the recommendation when health names no provider", async () => {
    stub({ status: "healthy", services: {} })
    render(<BackendStatusPill />, { wrapper })
    expect(await screen.findByText("Cloud")).toBeInTheDocument()
  })
})
