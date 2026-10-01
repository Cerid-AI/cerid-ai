// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * The chat model picker offers the models the host's own server serves.
 * It listed cloud models only, so an install that runs on local inference
 * had nothing in the picker it could use.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"

vi.mock("@/lib/api/routing", () => ({ fetchRoutingInfo: vi.fn() }))
vi.mock("@/lib/api/settings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/settings")>()),
  fetchModelCatalog: vi.fn().mockResolvedValue({ source: "unavailable", ids: [] }),
  fetchHealthStatus: vi.fn().mockResolvedValue({
    status: "healthy",
    services: {},
    local_model_server: { name: "MLX server", version: null, url: "http://models.test:11434" },
  }),
}))

import { fetchRoutingInfo } from "@/lib/api/routing"
import { ModelSelect } from "@/components/chat/model-select"
import { MODELS } from "@/lib/types"

const mockRouting = fetchRoutingInfo as ReturnType<typeof vi.fn>

const routing = (over: Record<string, unknown>) => ({
  ollama_available: true,
  ollama_models: [] as string[],
  model_registry: { free: {}, cheap: {}, capable: {}, research: {}, expert: {} },
  default_internal_model: "",
  smart_routing_enabled: true,
  ...over,
})

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>
}

async function open() {
  await userEvent.click(screen.getByRole("combobox"))
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("ModelSelect: local models", () => {
  it("names the server that serves them", async () => {
    mockRouting.mockResolvedValue(routing({ ollama_models: ["gemma-4-26b-a4b"] }))
    render(wrap(<ModelSelect value={MODELS[0].id} onChange={vi.fn()} />))
    await waitFor(() => expect(mockRouting).toHaveBeenCalled())

    await open()
    expect(await screen.findByText("MLX server")).toBeInTheDocument()
    expect(screen.queryByText(/Ollama|Quenchforge/)).not.toBeInTheDocument()
  })

  it("lists each local model, and choosing one reports it under the ollama prefix", async () => {
    mockRouting.mockResolvedValue(routing({ ollama_models: ["gemma-4-26b-a4b", "qwen3.5-4b-instruct"] }))
    const onChange = vi.fn()
    render(wrap(<ModelSelect value={MODELS[0].id} onChange={onChange} />))
    await waitFor(() => expect(mockRouting).toHaveBeenCalled())

    await open()
    expect(await screen.findByText("Local")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("option", { name: /gemma-4-26b-a4b/ }))

    expect(onChange).toHaveBeenCalledWith("ollama/gemma-4-26b-a4b")
  })

  it("leaves a local model selectable when no cloud provider is configured", async () => {
    mockRouting.mockResolvedValue(routing({ ollama_models: ["gemma-4-26b-a4b"] }))
    render(wrap(<ModelSelect value={MODELS[0].id} onChange={vi.fn()} configuredProviders={[]} />))
    await waitFor(() => expect(mockRouting).toHaveBeenCalled())

    await open()
    const local = await screen.findByRole("option", { name: /gemma-4-26b-a4b/ })
    expect(local).not.toHaveAttribute("data-disabled")
    const cloud = screen.getByRole("option", { name: new RegExp(MODELS[0].label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) })
    expect(cloud).toHaveAttribute("data-disabled")
  })

  it.each([
    ["the server serves none", routing({ ollama_models: [] })],
    ["the server is not available", routing({ ollama_available: false, ollama_models: ["gemma-4-26b-a4b"] })],
  ])("shows no Local group when %s", async (_name, info) => {
    mockRouting.mockResolvedValue(info)
    render(wrap(<ModelSelect value={MODELS[0].id} onChange={vi.fn()} />))
    await waitFor(() => expect(mockRouting).toHaveBeenCalled())

    await open()
    expect(screen.queryByText("Local")).not.toBeInTheDocument()
    expect(screen.queryByRole("option", { name: /gemma-4-26b-a4b/ })).not.toBeInTheDocument()
  })

  it("shows a selected local model once, in its group", async () => {
    mockRouting.mockResolvedValue(routing({ ollama_models: ["gemma-4-26b-a4b"] }))
    render(wrap(<ModelSelect value="ollama/gemma-4-26b-a4b" onChange={vi.fn()} />))
    await waitFor(() => expect(mockRouting).toHaveBeenCalled())

    await open()
    await screen.findByText("Local")
    expect(screen.getAllByRole("option", { name: /gemma-4-26b-a4b/ })).toHaveLength(1)
    expect(screen.queryByText("Current")).not.toBeInTheDocument()
  })
})
