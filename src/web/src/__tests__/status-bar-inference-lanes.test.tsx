// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F362 / F006 / F168 / F242 / F363 / F243 / F371 — the status bar reads the
 * degradation data and must render the verdict it actually supports.
 *
 * The payload below is the shape a Quenchforge box serves: every pipeline
 * stage configured local, the rerank lane answered by the CPU ONNX fallback
 * with an open circuit, and no ``internal_llm_provider`` / ``internal_llm_model``
 * keys — ``/health/status`` has never emitted those two.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StatusBar } from "@/components/layout/status-bar"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const QUENCHFORGE_STAGES = {
  claim_extraction: "quenchforge",
  query_decomposition: "quenchforge",
  topic_extraction: "quenchforge",
  memory_resolution: "quenchforge",
  reranking: "onnx",
  verification_simple: "quenchforge",
  verification_complex: "quenchforge",
  chat_generation: "quenchforge",
}

const mockQuenchforgeDegraded = {
  status: "healthy",
  services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
  degradation_tier: "full",
  pipeline_providers: QUENCHFORGE_STAGES,
  inference_routing: {
    llm: {
      provider: "quenchforge",
      url: "http://localhost:11434",
      model: "llama3.1-8b",
      serving: "quenchforge",
      serving_model: "qwen2.5-7b-instruct-q4_k_m",
      degraded: false,
      fallback_count: 4,
    },
    embed: {
      provider: "quenchforge",
      model: "nomic-embed-text-v1.5",
      serving: "quenchforge",
      degraded: false,
      fallback_count: 0,
    },
    rerank: {
      provider: "quenchforge",
      model: "bge-reranker-v2-m3",
      serving: "onnx",
      degraded: true,
      degraded_detail: "Circuit 'quenchforge-rerank' is open, retry after 8s",
      fallback_count: 44,
    },
  },
}

function stubHealth(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const payload = url.includes("credits") ? { configured: false } : body
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) })
    }),
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("StatusBar — inference lane truth", () => {
  it("names the backend from the LLM lane, not the absent internal_llm_provider key", async () => {
    stubHealth(mockQuenchforgeDegraded)
    render(<StatusBar />, { wrapper })
    const chip = await screen.findByTestId("local-pipeline-chip")
    expect(chip).toHaveTextContent(/Quenchforge/)
    expect(chip).not.toHaveTextContent(/Ollama/)
  })

  it("shows the model the daemon actually served, not 'active'", async () => {
    stubHealth(mockQuenchforgeDegraded)
    render(<StatusBar />, { wrapper })
    const chip = await screen.findByTestId("local-pipeline-chip")
    expect(chip).toHaveTextContent(/qwen2\.5-7b-instruct-q4_k_m/)
  })

  it("does not count the ONNX-fallback rerank stage as a healthy local stage", async () => {
    stubHealth(mockQuenchforgeDegraded)
    render(<StatusBar />, { wrapper })
    const chip = await screen.findByTestId("local-pipeline-chip")
    // 8 stages run on-box, but one of them is a degraded fallback.
    expect(chip).toHaveTextContent(/8\/8 local/)
    expect(chip).toHaveTextContent(/1 degraded/)
  })

  it("renders the per-lane routing snapshot instead of 'Routing snapshot available'", async () => {
    stubHealth(mockQuenchforgeDegraded)
    const user = userEvent.setup()
    render(<StatusBar />, { wrapper })
    const chip = await screen.findByTestId("local-pipeline-chip")
    await user.hover(chip)
    await waitFor(() =>
      expect(screen.getAllByTestId("inference-lane-rerank").length).toBeGreaterThan(0),
    )
    const rerank = screen.getAllByTestId("inference-lane-rerank")[0]
    expect(rerank).toHaveTextContent(/bge-reranker-v2-m3/)
    expect(rerank).toHaveTextContent(/ONNX/i)
    expect(rerank).toHaveTextContent(/44 fallbacks/)
    expect(rerank).toHaveTextContent(/Circuit 'quenchforge-rerank' is open/)
    expect(screen.queryByText(/Routing snapshot available/i)).not.toBeInTheDocument()
  })

  it("does not report 'all systems operational' while a lane serves from its fallback", async () => {
    stubHealth(mockQuenchforgeDegraded)
    render(<StatusBar />, { wrapper })
    const verdict = await screen.findByTestId("status-bar-verdict")
    await waitFor(() => expect(verdict).toHaveTextContent(/Reranking/i))
    expect(verdict.textContent ?? "").not.toMatch(/operational/i)
  })

  it("scopes its green verdict to what it measures when nothing is degraded", async () => {
    stubHealth({
      status: "healthy",
      services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
      inference_routing: {
        llm: { provider: "ollama", model: "llama3.2:3b", serving: "ollama", degraded: false, fallback_count: 0 },
      },
    })
    render(<StatusBar />, { wrapper })
    const verdict = await screen.findByTestId("status-bar-verdict")
    await waitFor(() => expect(verdict).toHaveTextContent(/connected/i))
    // "All systems operational" is a whole-system claim this dot cannot make:
    // it sees datastores and inference lanes, not latency or verification.
    expect(verdict.textContent ?? "").not.toMatch(/All systems operational/i)
  })

  it("names the backend the pipeline table names when the backend reports no lanes at all", async () => {
    stubHealth({
      status: "healthy",
      services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
      pipeline_providers: QUENCHFORGE_STAGES,
    })
    render(<StatusBar />, { wrapper })
    const chip = await screen.findByTestId("local-pipeline-chip")
    expect(chip).not.toHaveTextContent(/Ollama/)
    expect(chip).toHaveTextContent(/Quenchforge/)
  })

  it("falls back to a neutral label when nothing names a local LLM backend", async () => {
    stubHealth({
      status: "healthy",
      services: { chromadb: "connected", redis: "connected", neo4j: "connected" },
      pipeline_providers: { reranking: "onnx", embedding: "onnx" },
    })
    render(<StatusBar />, { wrapper })
    const chip = await screen.findByTestId("local-pipeline-chip")
    expect(chip).not.toHaveTextContent(/Ollama/)
    expect(chip).toHaveTextContent(/Local inference/i)
  })
})
