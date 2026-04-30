// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests for InferenceModelsCard (Workstream E Phase E.6.3).

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { InferenceModelsCard } from "@/components/settings/inference-models-card"
import * as settingsApi from "@/lib/api/settings"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("InferenceModelsCard", () => {
  it("renders cached badges when both models present", async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: {
        repo: "cross-encoder/ms-marco-MiniLM-L-6-v2",
        cached: true,
        files: { "onnx/model.onnx": "/cache/x", "tokenizer.json": "/cache/y" },
      },
      embedder: {
        repo: "Snowflake/snowflake-arctic-embed-m-v1.5",
        cached: true,
        files: { "onnx/model.onnx": "/cache/a", "tokenizer.json": "/cache/b" },
      },
    })
    render(<InferenceModelsCard />, { wrapper })
    await waitFor(() =>
      expect(screen.getAllByText(/cached/i).length).toBeGreaterThanOrEqual(2),
    )
    // Cached state shows the "Re-warm cache" button instead of "Download"
    expect(screen.getByRole("button", { name: /re-warm cache/i })).toBeInTheDocument()
  })

  it("renders not-cached badges + download button when both missing", async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: {
        repo: "cross-encoder/ms-marco-MiniLM-L-6-v2",
        cached: false,
        files: { "onnx/model.onnx": null, "tokenizer.json": null },
      },
      embedder: {
        repo: "Snowflake/snowflake-arctic-embed-m-v1.5",
        cached: false,
        files: { "onnx/model.onnx": null, "tokenizer.json": null },
      },
    })
    render(<InferenceModelsCard />, { wrapper })
    await waitFor(() =>
      expect(screen.getAllByText(/not cached/i).length).toBeGreaterThanOrEqual(2),
    )
    expect(
      screen.getByRole("button", { name: /download models/i }),
    ).toBeInTheDocument()
    // Helper hint is rendered when nothing is cached
    expect(
      screen.getByText(/first semantic query after startup/i),
    ).toBeInTheDocument()
  })

  it("triggers preload + shows result line on success", async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: false, files: {} },
      embedder: { repo: "e", cached: false, files: {} },
    })
    const preloadSpy = vi.spyOn(settingsApi, "preloadModels").mockResolvedValue({
      status: "ok",
      reranker_status: "loaded",
      reranker_ms: 8200,
      embedder_status: "loaded",
      embedder_ms: 12100,
      total_ms: 20300,
    })
    const user = userEvent.setup()
    render(<InferenceModelsCard />, { wrapper })

    const button = await screen.findByRole("button", { name: /download models/i })
    await user.click(button)

    await waitFor(() => expect(preloadSpy).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/Models loaded/i),
    )
    expect(screen.getByRole("status")).toHaveTextContent(/Total 20300ms/i)
  })

  it("renders partial-status warning when one loader fails", async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: false, files: {} },
      embedder: { repo: "e", cached: false, files: {} },
    })
    vi.spyOn(settingsApi, "preloadModels").mockResolvedValue({
      status: "partial",
      reranker_status: "failed",
      reranker_error: "simulated HuggingFace outage",
      embedder_status: "loaded",
      embedder_ms: 12000,
      total_ms: 12500,
    })
    const user = userEvent.setup()
    render(<InferenceModelsCard />, { wrapper })

    const button = await screen.findByRole("button", { name: /download models/i })
    await user.click(button)

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/Partial/i),
    )
    expect(screen.getByRole("status")).toHaveTextContent(/simulated HuggingFace outage/)
  })

  it("renders error banner when preload throws", async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: false, files: {} },
      embedder: { repo: "e", cached: false, files: {} },
    })
    vi.spyOn(settingsApi, "preloadModels").mockRejectedValue(
      new Error("network unreachable"),
    )
    const user = userEvent.setup()
    render(<InferenceModelsCard />, { wrapper })

    const button = await screen.findByRole("button", { name: /download models/i })
    await user.click(button)

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/network unreachable/i),
    )
  })
})
