// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * "ollama" and "quenchforge" are provider ids: they say which API the instance
 * speaks. The server that answers reports its own name, and the shell shows
 * that name.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StatusBar } from "@/components/layout/status-bar"
import { BackendStatusPill } from "@/components/layout/backend-status-pill"
import { TooltipProvider } from "@/components/ui/tooltip"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}

const STAGES = {
  claim_extraction: "ollama",
  query_decomposition: "ollama",
  chat_generation: "ollama",
}

function health(serverName: string) {
  return {
    status: "healthy",
    services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
    degradation_tier: "full",
    internal_llm_provider: "ollama",
    local_model_server: {
      name: serverName,
      version: "cerid-mlx-c33d6c31d1f1",
      url: "http://host.docker.internal:11434",
    },
    pipeline_providers: STAGES,
    inference_routing: {
      llm: {
        provider: "ollama",
        model: "gemma-4-26b-a4b",
        serving: "ollama",
        serving_model: "gemma-4-26b-a4b",
        degraded: false,
        fallback_count: 0,
      },
      embed: {
        provider: "quenchforge",
        model: "nomic-embed-text-v1.5",
        serving: "quenchforge",
        degraded: false,
        fallback_count: 0,
      },
      rerank: { provider: "in-process", serving: "onnx", degraded: false, fallback_count: 0 },
    },
  }
}

const SYSTEM_CHECK = {
  ram_gb: 64,
  os: "macOS",
  cpu: "Apple M4 Max",
  gpu: "Apple M4 Max",
  gpu_acceleration: "metal",
  recommended_local_backend: "ollama",
  ollama_detected: true,
  ollama_models: ["gemma-4-26b-a4b"],
}

function stub(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const payload = url.includes("credits")
        ? { configured: false }
        : url.includes("/system-check")
          ? SYSTEM_CHECK
          : body
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(payload),
        text: () => Promise.resolve(JSON.stringify(payload)),
      })
    }),
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("the local model server's name", () => {
  it("is what the status bar chip shows", async () => {
    stub(health("MLX server"))
    render(<StatusBar />, { wrapper })
    const chip = await screen.findByTestId("local-pipeline-chip")
    expect(chip).toHaveTextContent(/MLX server/)
    expect(chip).not.toHaveTextContent(/Ollama|Quenchforge/)
  })

  it("is what each lane served by that server shows", async () => {
    stub(health("MLX server"))
    const user = userEvent.setup()
    render(<StatusBar />, { wrapper })
    await user.hover(await screen.findByTestId("local-pipeline-chip"))
    await waitFor(() =>
      expect(screen.getAllByTestId("inference-lane-embed").length).toBeGreaterThan(0),
    )
    const embed = screen.getAllByTestId("inference-lane-embed")[0]
    expect(embed).toHaveTextContent(/Embeddings: MLX server/)
    expect(embed).toHaveTextContent(/Serving MLX server/)
    expect(embed).not.toHaveTextContent(/Quenchforge/)
    // A lane served in-process is not the local server.
    expect(screen.getAllByTestId("inference-lane-rerank")[0]).toHaveTextContent(/In-process/)
  })

  it("is what the backend pill shows", async () => {
    stub(health("MLX server"))
    render(<BackendStatusPill />, { wrapper })
    const pill = await screen.findByTestId("backend-status-pill")
    await waitFor(() => expect(pill).toHaveTextContent("MLX server"))
    expect(pill).not.toHaveTextContent(/Ollama/)
  })

  it("is neutral when the server does not say what it is", async () => {
    stub(health("Local model server"))
    render(<StatusBar />, { wrapper })
    const chip = await screen.findByTestId("local-pipeline-chip")
    expect(chip).toHaveTextContent(/Local model server/)
    expect(chip).not.toHaveTextContent(/Ollama/)
  })
})
