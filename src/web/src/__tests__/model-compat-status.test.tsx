// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests for ModelCompatStatus — the /models/doctor compatibility surface
// wired into Settings → Models and the setup wizard. Covers the 4-state
// matrix (loading/error/empty-clear/findings) + compact mode + axe.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"

import { ModelCompatStatus } from "@/components/settings/model-compat-status"
import * as settingsApi from "@/lib/api/settings"
import type { ModelDoctorReport } from "@/lib/api/settings"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const CLEAN: ModelDoctorReport = {
  hardware_profile: "amd-mac",
  ok: true,
  findings: [],
  known_good_local: { chat: "llama3.1-8b", embed: "nomic-embed-text-v1.5", rerank: "bge-reranker-v2-m3" },
  candidate_upgrades: { chat: [], embed: [], rerank: [] },
  catalog_size: 320,
}

const INCOMPATIBLE: ModelDoctorReport = {
  ...CLEAN,
  ok: false,
  findings: [
    {
      kind: "incompatible",
      severity: "error",
      role: "INTERNAL_LLM_MODEL",
      model: "llama3.2:3b",
      detail: "crashes the Vega II Metal stack (GGML_ASSERT(buf_dst)); use a known-good model such as llama3.1-8b",
    },
  ],
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("ModelCompatStatus", () => {
  it("shows the all-compatible success state when there are no findings", async () => {
    vi.spyOn(settingsApi, "fetchModelDoctor").mockResolvedValue(CLEAN)
    render(<ModelCompatStatus />, { wrapper })
    await waitFor(() =>
      expect(screen.getByText(/all configured models are compatible/i)).toBeInTheDocument()
    )
    expect(screen.getByText("amd-mac")).toBeInTheDocument()
  })

  it("renders an incompatible finding with its model + remediation detail", async () => {
    vi.spyOn(settingsApi, "fetchModelDoctor").mockResolvedValue(INCOMPATIBLE)
    render(<ModelCompatStatus />, { wrapper })
    await waitFor(() => expect(screen.getByText("INTERNAL_LLM_MODEL")).toBeInTheDocument())
    expect(screen.getByText("llama3.2:3b")).toBeInTheDocument()
    expect(screen.getByText(/crashes the Vega II Metal stack/i)).toBeInTheDocument()
  })

  it("shows an error + retry when the doctor request fails", async () => {
    vi.spyOn(settingsApi, "fetchModelDoctor").mockRejectedValue(new Error("boom"))
    render(<ModelCompatStatus />, { wrapper })
    await waitFor(() =>
      expect(screen.getByText(/couldn.t check model compatibility/i)).toBeInTheDocument()
    )
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })

  it("compact mode surfaces the profile + recommended local models", async () => {
    vi.spyOn(settingsApi, "fetchModelDoctor").mockResolvedValue(CLEAN)
    render(<ModelCompatStatus compact />, { wrapper })
    await waitFor(() => expect(screen.getByTestId("model-compat-compact")).toBeInTheDocument())
    expect(screen.getByText(/recommended local models/i)).toBeInTheDocument()
    expect(screen.getByText(/chat llama3.1-8b/i)).toBeInTheDocument()
  })

  it("is accessible in the success state", async () => {
    vi.spyOn(settingsApi, "fetchModelDoctor").mockResolvedValue(CLEAN)
    const { container } = render(<ModelCompatStatus />, { wrapper })
    await waitFor(() =>
      expect(screen.getByText(/all configured models are compatible/i)).toBeInTheDocument()
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
