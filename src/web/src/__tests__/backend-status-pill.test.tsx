// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F362 — the backend pill labelled itself from ``/system-check``'s hardware
 * *recommendation*, which is what the box could run, not what it is running.
 * On a machine recommended Quenchforge but configured for OpenRouter the pill
 * read "Quenchforge" forever, and a quenchforge host with a cloud
 * recommendation read "Cloud". The pill now reports, in order: the provider
 * that answered the last LLM call, the provider /health/status says is
 * configured, and only then the recommendation — marked as one.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { BackendStatusPill } from "@/components/layout/backend-status-pill"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@/components/ui/tooltip"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}

const SYSTEM_CHECK_QUENCHFORGE = {
  ram_gb: 64,
  os: "Darwin",
  cpu: "Intel",
  gpu: "AMD Radeon Pro Vega II",
  gpu_acceleration: "metal",
  recommended_local_backend: "quenchforge",
  ollama_detected: true,
  ollama_models: ["llama3.1-8b"],
}

const SYSTEM_CHECK_CLOUD = {
  ram_gb: 32, docker_running: true, env_exists: true, env_keys_present: [],
  ollama_detected: false, ollama_url: null, ollama_models: [],
  lightweight_recommended: false, archive_path_exists: true,
  default_archive_path: "/data", os: "macOS", cpu: "Intel Xeon W",
  cpu_cores: 16, gpu: "Radeon Pro Vega II", gpu_acceleration: "none",
  recommended_local_backend: "cloud",
}

function stub(routes: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const key = Object.keys(routes).find((k) => url.includes(k))
      const body = key ? routes[key] : {}
      return Promise.resolve({
        ok: true,
        status: 200,
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
  it("names the provider actually serving the LLM lane, not the hardware recommendation", async () => {
    stub({
      "/system-check": SYSTEM_CHECK_QUENCHFORGE,
      "/health/status": {
        status: "healthy",
        services: { chromadb: "connected" },
        inference_routing: {
          llm: {
            provider: "openrouter",
            model: "anthropic/claude-sonnet-4.6",
            serving: "openrouter",
            degraded: false,
            fallback_count: 0,
          },
        },
      },
    })
    render(<BackendStatusPill />, { wrapper })
    const pill = await screen.findByTestId("backend-status-pill")
    await waitFor(() => expect(pill).toHaveTextContent(/OpenRouter/i))
    expect(pill.textContent ?? "").not.toMatch(/Quenchforge/i)
  })

  it("flags the pill when the serving lane has fallen back", async () => {
    stub({
      "/system-check": SYSTEM_CHECK_QUENCHFORGE,
      "/health/status": {
        status: "healthy",
        services: { chromadb: "connected" },
        inference_routing: {
          llm: {
            provider: "quenchforge",
            model: "llama3.1-8b",
            serving: "openrouter",
            degraded: true,
            degraded_detail: "quenchforge chat slot unreachable",
            fallback_count: 12,
          },
        },
      },
    })
    render(<BackendStatusPill />, { wrapper })
    const pill = await screen.findByTestId("backend-status-pill")
    await waitFor(() => expect(pill).toHaveTextContent(/OpenRouter/i))
    expect(pill).toHaveTextContent(/fallback/i)
  })

  it("labels the configured quenchforge backend, not the recommendation", async () => {
    stub({
      "/system-check": SYSTEM_CHECK_CLOUD,
      "/health/status": { status: "healthy", services: {}, internal_llm_provider: "quenchforge" },
    })
    render(<BackendStatusPill />, { wrapper })
    expect(await screen.findByText("Quenchforge")).toBeInTheDocument()
    expect(screen.queryByText(/Cloud/)).not.toBeInTheDocument()
  })

  it("labels a configured ollama backend Ollama", async () => {
    stub({
      "/system-check": SYSTEM_CHECK_CLOUD,
      "/health/status": { status: "healthy", services: {}, internal_llm_provider: "ollama" },
    })
    render(<BackendStatusPill />, { wrapper })
    expect(await screen.findByText("Ollama")).toBeInTheDocument()
  })

  it("labels a configured openrouter backend Cloud", async () => {
    stub({
      "/system-check": SYSTEM_CHECK_CLOUD,
      "/health/status": { status: "healthy", services: {}, internal_llm_provider: "openrouter" },
    })
    render(<BackendStatusPill />, { wrapper })
    expect(await screen.findByText("Cloud")).toBeInTheDocument()
  })

  it("marks the label as a hardware recommendation when health names no provider and no lane has answered", async () => {
    stub({
      "/system-check": SYSTEM_CHECK_QUENCHFORGE,
      "/health/status": { status: "healthy", services: { chromadb: "connected" } },
    })
    render(<BackendStatusPill />, { wrapper })
    const pill = await screen.findByTestId("backend-status-pill")
    await waitFor(() => expect(pill).toHaveTextContent(/Quenchforge/i))
    expect(pill).toHaveTextContent(/recommended/i)
  })
})
