// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StatusBar } from "@/components/layout/status-bar"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { watchWebPort } from "@/lib/web-port"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const mockHealthy = {
  status: "healthy",
  services: {
    chromadb: "connected",
    redis: "connected",
    neo4j: "connected",
  },
}

const mockDegraded = {
  status: "degraded",
  services: {
    chromadb: "connected",
    redis: "error",
    neo4j: "connected",
  },
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("StatusBar", () => {
  it("renders service names in status text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockHealthy),
      }),
    )
    render(<StatusBar />, { wrapper })
    // Services render as "{name}: {state}" — e.g., "chromadb: connected"
    expect(await screen.findByText(/chromadb/i)).toBeInTheDocument()
    expect(screen.getByText(/redis/i)).toBeInTheDocument()
    expect(screen.getByText(/neo4j/i)).toBeInTheDocument()
  })

  // The dot reads datastore transports and inference lanes — not latency or
  // verification coverage, which Diagnostics grades separately. Its label
  // states that scope instead of claiming the whole system is operational.
  it("shows a scoped healthy status message when all services connected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockHealthy),
      }),
    )
    render(<StatusBar />, { wrapper })
    expect(await screen.findByText("All services connected")).toBeInTheDocument()
  })

  it("shows degraded status message when services have errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockDegraded),
      }),
    )
    render(<StatusBar />, { wrapper })
    expect(await screen.findByText("Some services degraded")).toBeInTheDocument()
  })

  // CR-069: Bifrost was retired 2026-04-17 — the OpenRouter failure tooltips
  // must not promise a fallback gateway that no longer exists.
  it("CR-069: OpenRouter auth-error tooltip does not promise a Bifrost fallback", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...mockHealthy, openrouter_auth_ok: false }),
        }),
      ),
    )
    const user = userEvent.setup()
    render(<StatusBar />, { wrapper })
    const badge = await screen.findByText(/OpenRouter: Auth Error/i)
    await user.hover(badge)
    await waitFor(() => expect(screen.getAllByText(/no fallback gateway/i).length).toBeGreaterThan(0))
    expect(screen.queryAllByText(/Bifrost/i)).toHaveLength(0)
  })

  it("CR-069: OpenRouter circuit-open tooltip does not promise a Bifrost fallback", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              ...mockHealthy,
              openrouter_auth_ok: true,
              circuit_breakers: { openrouter: "open" },
            }),
        }),
      ),
    )
    const user = userEvent.setup()
    render(<StatusBar />, { wrapper })
    const badge = await screen.findByText(/OpenRouter: Circuit Open/i)
    await user.hover(badge)
    await waitFor(() => expect(screen.getAllByText(/circuit resets/i).length).toBeGreaterThan(0))
    expect(screen.queryAllByText(/Bifrost/i)).toHaveLength(0)
  })

  // CH-CREDITS: a recovered "ok" credits status must not render the stale
  // "Credits exhausted" footer.
  it("does not show 'Credits exhausted' when provider credits status is ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const body = url.includes("/credits") || url.includes("provider")
          ? { configured: true, provider: "openrouter", balance: 39.84, status: "ok" }
          : mockHealthy
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
      }),
    )
    render(<StatusBar />, { wrapper })
    expect(await screen.findByText("$39.84")).toBeInTheDocument()
    expect(screen.queryByText("Credits exhausted")).not.toBeInTheDocument()
  })
})


describe("StatusBar — local pipeline label", () => {
  function stubHealth(payload: Record<string, unknown>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...mockHealthy, ...payload }),
        }),
      ),
    )
  }

  it("labels quenchforge pipeline stages Quenchforge", async () => {
    stubHealth({
      internal_llm_provider: "quenchforge",
      internal_llm_model: "llama3.1:8b",
      pipeline_providers: { chat_generation: "quenchforge", reranking: "quenchforge" },
    })
    render(<StatusBar />, { wrapper })
    expect(await screen.findByText(/Quenchforge: llama3\.1:8b/)).toBeInTheDocument()
  })

  it("labels ollama pipeline stages Ollama", async () => {
    stubHealth({
      internal_llm_provider: "ollama",
      internal_llm_model: "llama3.2:3b",
      pipeline_providers: { chat_generation: "ollama", reranking: "ollama" },
    })
    render(<StatusBar />, { wrapper })
    expect(await screen.findByText(/Ollama: llama3\.2:3b/)).toBeInTheDocument()
  })

  // The live regression: /health omitted internal_llm_provider entirely, so a
  // quenchforge host read "Ollama: active" off the default branch.
  it("falls back to the provider the pipeline table names, not to Ollama", async () => {
    stubHealth({
      internal_llm_model: "llama3.1:8b",
      pipeline_providers: { chat_generation: "quenchforge", reranking: "quenchforge" },
    })
    render(<StatusBar />, { wrapper })
    expect(await screen.findByText(/Quenchforge: llama3\.1:8b/)).toBeInTheDocument()
    expect(screen.queryByText(/Ollama: /)).not.toBeInTheDocument()
  })
})

describe("StatusBar — API unreachable (audit 51)", () => {
  it("drops the last known service and model states once the health check fails", async () => {
    let apiUp = true
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        apiUp
          ? Promise.resolve({
              ok: true,
              status: 200,
              json: () =>
                Promise.resolve({
                  ...mockHealthy,
                  internal_llm_model: "llama3.1:8b",
                  pipeline_providers: { chat_generation: "ollama" },
                }),
            })
          : Promise.resolve({ ok: false, status: 502, json: () => Promise.resolve({}) }),
      ),
    )
    const queryClient = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
    render(
      <QueryClientProvider client={queryClient}>
        <StatusBar />
      </QueryClientProvider>,
    )
    expect(await screen.findByText("chromadb: connected")).toBeInTheDocument()
    expect(screen.getByTestId("local-pipeline-chip")).toBeInTheDocument()

    apiUp = false
    await queryClient.refetchQueries({ queryKey: ["health-status"] })

    expect(await screen.findByText("Connection error")).toBeInTheDocument()
    expect(screen.queryAllByText(/: connected/)).toHaveLength(0)
    expect(screen.queryByTestId("local-pipeline-chip")).not.toBeInTheDocument()
  })

  it("gives the health check a deadline, so a hung gateway is reported", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<unknown>>(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mockHealthy) }),
    )
    vi.stubGlobal("fetch", fetchMock)
    render(<StatusBar />, { wrapper })
    await screen.findByText("All services connected")

    const call = fetchMock.mock.calls.find(([url]) => url.includes("/health/status"))
    expect(call?.[1]?.signal).toBeInstanceOf(AbortSignal)
  })
})

// jsdom does no layout, so this pins the rule rather than the pixels. Measured
// in a browser at 768 px: the labels wrapped inside a bar fixed at 32 px and
// were cut off at the bottom.
describe("StatusBar — narrow window (audit 52)", () => {
  it("wraps whole items onto a new row instead of breaking and clipping their text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve(mockHealthy) }),
    )
    render(<StatusBar />, { wrapper })
    const bar = (await screen.findByTestId("status-bar-verdict")).parentElement!.parentElement!
    expect(bar).toHaveClass("flex-wrap", "whitespace-nowrap", "min-h-8")
    expect(bar).not.toHaveClass("h-8")
  })
})

describe("StatusBar — web port sign-in", () => {
  async function proxySays(access: string) {
    const target = {
      fetch: (async () =>
        new Response("{}", { headers: { "X-Cerid-Port-Access": access } })) as typeof fetch,
    }
    watchWebPort({ target, navigate: vi.fn() })
    await target.fetch("/api/mcp/health/status")
  }

  function stubHealthy() {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockHealthy),
      }),
    )
  }

  it("says the port is open to local processes when no password is set", async () => {
    stubHealthy()
    await proxySays("open")
    render(<StatusBar />, { wrapper })

    const notice = await screen.findByText("Port open: no sign-in")
    await userEvent.hover(notice)
    const tips = await screen.findAllByText(/any process on this machine can use the API/i)
    expect(tips.length).toBeGreaterThan(0)
  })

  it("says nothing once a sign-in is enforced", async () => {
    stubHealthy()
    await proxySays("signed-in")
    render(<StatusBar />, { wrapper })

    await screen.findByText("All services connected")
    expect(screen.queryByText("Port open: no sign-in")).not.toBeInTheDocument()
  })
})
