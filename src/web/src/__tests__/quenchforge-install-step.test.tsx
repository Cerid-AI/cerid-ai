// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F180 / F241 — the step called the install done when any daemon answered
 * /api/tags with at least one model (`ollama_detected`, set in setup.py as
 * `len(ollama_models) > 0`). Nothing pulled a model, nothing set the
 * per-slot model names, and nothing probed the rerank slot, so the exact
 * broken state — LLM model unset, rerank 503ing to the CPU fallback — showed
 * a green "Detected" badge and let the wizard advance.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@/components/ui/tooltip"
import { QuenchforgeInstallStep } from "@/components/setup/quenchforge-install-step"
import type { SystemCheckResponse } from "@/lib/types"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}

const DETECTED = {
  ram_gb: 64,
  os: "Darwin",
  cpu: "Intel",
  gpu: "AMD Radeon Pro Vega II",
  gpu_acceleration: "metal",
  ollama_detected: true,
  ollama_url: "http://localhost:11434",
  ollama_models: ["nomic-embed-text-v1.5", "qwen2.5-7b-instruct-q4_k_m"],
} as unknown as SystemCheckResponse

let fetchMock: ReturnType<typeof vi.fn>

function stubHealth(body: unknown) {
  fetchMock = vi.fn(() =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) }),
  )
  vi.stubGlobal("fetch", fetchMock)
}

const NOOP = () => {}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("QuenchforgeInstallStep", () => {
  it("does not call the install done when the LLM slot has no model and rerank has fallen back", async () => {
    stubHealth({
      status: "healthy",
      services: { chromadb: "connected" },
      inference_routing: {
        llm: { provider: "quenchforge", model: "unset", serving: "quenchforge", degraded: false, fallback_count: 4 },
        rerank: {
          provider: "quenchforge",
          model: "bge-reranker-v2-m3",
          serving: "onnx",
          degraded: true,
          degraded_detail: "Circuit 'quenchforge-rerank' is open",
          fallback_count: 31,
        },
      },
    })
    render(
      <QuenchforgeInstallStep systemCheck={DETECTED} onSystemCheckRefresh={NOOP} />,
      { wrapper },
    )
    const badge = await screen.findByTestId("quenchforge-status-badge")
    await waitFor(() => expect(badge).toHaveTextContent(/not ready/i))
    expect(badge.className).not.toMatch(/green/)
  })

  it("names each slot that is not usable", async () => {
    stubHealth({
      status: "healthy",
      services: { chromadb: "connected" },
      inference_routing: {
        llm: { provider: "quenchforge", model: "unset", serving: "quenchforge", degraded: false, fallback_count: 4 },
        rerank: {
          provider: "quenchforge",
          model: "bge-reranker-v2-m3",
          serving: "onnx",
          degraded: true,
          degraded_detail: "Circuit 'quenchforge-rerank' is open",
          fallback_count: 31,
        },
      },
    })
    render(
      <QuenchforgeInstallStep systemCheck={DETECTED} onSystemCheckRefresh={NOOP} />,
      { wrapper },
    )
    const problems = await screen.findByTestId("quenchforge-slot-problems")
    expect(problems).toHaveTextContent(/LLM/i)
    expect(problems).toHaveTextContent(/Reranking/i)
    expect(problems).toHaveTextContent(/Circuit 'quenchforge-rerank' is open/)
  })

  it("reports ready once every configured slot serves a pinned model", async () => {
    stubHealth({
      status: "healthy",
      services: { chromadb: "connected" },
      inference_routing: {
        llm: { provider: "quenchforge", model: "llama3.1-8b", serving: "quenchforge", degraded: false, fallback_count: 0 },
        embed: { provider: "quenchforge", model: "nomic-embed-text-v1.5", serving: "quenchforge", degraded: false, fallback_count: 0 },
        rerank: { provider: "quenchforge", model: "bge-reranker-v2-m3", serving: "quenchforge", degraded: false, fallback_count: 0 },
      },
    })
    render(
      <QuenchforgeInstallStep systemCheck={DETECTED} onSystemCheckRefresh={NOOP} />,
      { wrapper },
    )
    const badge = await screen.findByTestId("quenchforge-status-badge")
    await waitFor(() => expect(badge).toHaveTextContent("Ready"))
    expect(badge.className).toMatch(/green/)
    expect(screen.queryByTestId("quenchforge-slot-problems")).not.toBeInTheDocument()
  })

  it("says the daemon answered but nothing has been observed when no lane has served", async () => {
    stubHealth({ status: "healthy", services: { chromadb: "connected" } })
    render(
      <QuenchforgeInstallStep systemCheck={DETECTED} onSystemCheckRefresh={NOOP} />,
      { wrapper },
    )
    const badge = await screen.findByTestId("quenchforge-status-badge")
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    await screen.findByTestId("quenchforge-slot-unverified")
    expect(badge.className).not.toMatch(/green/)
    expect(badge).toHaveTextContent(/not verified|unverified|not ready/i)
  })

  it("still reports 'not detected' when no daemon answers at all", async () => {
    stubHealth({ status: "degraded", services: {} })
    render(
      <QuenchforgeInstallStep
        systemCheck={{ ...DETECTED, ollama_detected: false, ollama_models: [] }}
        onSystemCheckRefresh={NOOP}
      />,
      { wrapper },
    )
    const badge = await screen.findByTestId("quenchforge-status-badge")
    expect(badge).toHaveTextContent(/not detected/i)
  })
})
