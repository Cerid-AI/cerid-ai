// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import type { EgressReport } from "@/lib/types"

vi.mock("@/lib/api", () => ({
  fetchEgressReport: vi.fn(),
}))

import { fetchEgressReport } from "@/lib/api"
import { DataEgressSection } from "@/components/settings/data-egress-section"

const mockFetchEgressReport = fetchEgressReport as ReturnType<typeof vi.fn>

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function makeReport(): EgressReport {
  return {
    egress: [
      {
        channel: "kb_backup_sync",
        destination: "/data/sync (local directory)",
        trigger: "on sync",
        payload_class: "KB JSONL",
        status: "local",
        setting_key: "SYNC_DIR",
      },
      {
        channel: "model_downloads",
        destination: "HuggingFace Hub (huggingface.co)",
        trigger: "first use of an uncached model",
        payload_class: "none (model weight downloads)",
        status: "external_off",
        setting_key: "CERID_PRELOAD_MODELS",
      },
      {
        channel: "chat_llm",
        destination: "OpenRouter (openrouter.ai)",
        trigger: "every chat message",
        payload_class: "query + conversation context",
        status: "external_on",
        setting_key: "OPENROUTER_API_KEY",
      },
    ],
  }
}

beforeEach(() => {
  mockFetchEgressReport.mockReset()
  mockFetchEgressReport.mockResolvedValue(makeReport())
})

// ---------------------------------------------------------------------------
// D.2: four-state matrix
// ---------------------------------------------------------------------------

describe("DataEgressSection — four-state matrix (D.2)", () => {
  it("loading: renders skeleton rows shaped like the table while pending", () => {
    mockFetchEgressReport.mockReturnValue(new Promise(() => {})) // never resolves
    const { container } = render(<DataEgressSection />, { wrapper: makeWrapper() })
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)
  })

  it("error: shows a destructive Alert with a Retry button that calls refetch", async () => {
    mockFetchEgressReport.mockRejectedValue(new Error("Connection refused"))
    render(<DataEgressSection />, { wrapper: makeWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/Failed to load the egress report/i)).toBeInTheDocument()
    })

    mockFetchEgressReport.mockResolvedValueOnce(makeReport())
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))

    await waitFor(() => expect(mockFetchEgressReport).toHaveBeenCalledTimes(2))
    expect(await screen.findByText("Local")).toBeInTheDocument()
  })

  it("empty: shows EmptyState when the server reports zero channels", async () => {
    mockFetchEgressReport.mockResolvedValue({ egress: [] })
    render(<DataEgressSection />, { wrapper: makeWrapper() })
    expect(await screen.findByText(/No egress channels reported/i)).toBeInTheDocument()
  })

  it("success: renders every row with its human channel label", async () => {
    render(<DataEgressSection />, { wrapper: makeWrapper() })
    expect(await screen.findByText("Knowledge-base sync")).toBeInTheDocument()
    expect(screen.getByText("Model downloads")).toBeInTheDocument()
    expect(screen.getByText("Chat LLM")).toBeInTheDocument()
  })

  it("success: maps each status to the canonical badge text + colour tokens", async () => {
    render(<DataEgressSection />, { wrapper: makeWrapper() })

    const local = await screen.findByText("Local")
    expect(local.className).toContain("bg-green-500/10")
    expect(local.className).toContain("text-green-600")

    const externalOff = screen.getByText("External · off")
    expect(externalOff.className).toContain("bg-amber-500/10")
    expect(externalOff.className).toContain("text-amber-600")

    const externalOn = screen.getByText("External · on")
    expect(externalOn.className).toContain("bg-red-500/10")
    expect(externalOn.className).toContain("text-red-600")
  })
})

// ---------------------------------------------------------------------------
// D.3: axe-clean across all four states
// ---------------------------------------------------------------------------

describe("DataEgressSection — axe-clean (D.3)", () => {
  it("is axe-clean in loading state", async () => {
    mockFetchEgressReport.mockReturnValue(new Promise(() => {}))
    const { container } = render(<DataEgressSection />, { wrapper: makeWrapper() })
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in error state", async () => {
    mockFetchEgressReport.mockRejectedValue(new Error("fail"))
    const { container } = render(<DataEgressSection />, { wrapper: makeWrapper() })
    await waitFor(() => screen.getByText(/Failed to load the egress report/i))
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in empty state", async () => {
    mockFetchEgressReport.mockResolvedValue({ egress: [] })
    const { container } = render(<DataEgressSection />, { wrapper: makeWrapper() })
    await screen.findByText(/No egress channels reported/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in success state", async () => {
    const { container } = render(<DataEgressSection />, { wrapper: makeWrapper() })
    await screen.findByText("Local")
    expect(await axe(container)).toHaveNoViolations()
  })
})
