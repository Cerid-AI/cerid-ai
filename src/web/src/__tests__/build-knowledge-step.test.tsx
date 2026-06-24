// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"

// ---------------------------------------------------------------------------
// Mock the knowledge-packs API — two domains, one multi-MB pack, one planned
// ---------------------------------------------------------------------------
vi.mock("@/lib/api/knowledge-packs", () => ({
  fetchKnowledgePackRegistry: vi.fn().mockResolvedValue({
    schema_version: 1,
    packs_by_domain: {
      coding: [
        {
          id: "python-stdlib-docs",
          name: "Python Standard Library Documentation",
          version: "1.0.0",
          description: "Authoritative Python stdlib reference.",
          domain: "coding",
          sub_category: "python",
          tags: ["python"],
          license: "PSF-2.0",
          size_bytes: 12_345_678,   // multi-MB pack
          artifact_count: 208,
          download_url: "https://example.com/pystd.tar.gz",
          sha256: "abc123",
          provenance: { status: "built" },
        },
      ],
      finance: [
        {
          id: "bogleheads-wiki",
          name: "Bogleheads Wiki",
          version: "0.1.0",
          description: "Personal finance wiki — planned.",
          domain: "finance",
          sub_category: "investing",
          tags: ["finance"],
          license: "CC-BY-SA",
          size_bytes: 0,
          artifact_count: 0,
          download_url: "",
          sha256: "",
          provenance: { status: "planned" },
        },
      ],
    },
  }),
  installKnowledgePack: vi.fn().mockResolvedValue({
    pack_id: "python-stdlib-docs",
    version: "1.0.0",
    installed_at: "2026-06-24T12:00:00Z",
    domain: "coding",
    artifact_count: 208,
  }),
}))

import { BuildKnowledgeStep } from "@/components/setup/build-knowledge-step"
import { installKnowledgePack } from "@/lib/api/knowledge-packs"

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function renderStep(overrides?: {
  state?: Parameters<typeof BuildKnowledgeStep>[0]["state"]
  onChange?: Parameters<typeof BuildKnowledgeStep>[0]["onChange"]
}) {
  const defaultState = {
    installedPackIds: [] as string[],
    firstDoc: { ingested: false, queried: false, skipped: false, documentCount: 0 },
  }
  const onChange = overrides?.onChange ?? vi.fn()
  const state = overrides?.state ?? defaultState

  return render(
    <QueryClientProvider client={makeClient()}>
      <BuildKnowledgeStep state={state} onChange={onChange} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("BuildKnowledgeStep", () => {
  it("renders the step heading", async () => {
    renderStep()
    expect(screen.getByText(/Build Knowledge/i)).toBeInTheDocument()
  })

  it("renders packs grouped by domain after registry loads", async () => {
    renderStep()
    // Domain headers — use getAllByText since DomainBadge + description may both match
    await waitFor(() => {
      expect(screen.getAllByText(/coding/i).length).toBeGreaterThanOrEqual(1)
    })
    expect(screen.getAllByText(/finance/i).length).toBeGreaterThanOrEqual(1)
    // Pack names
    expect(screen.getByText("Python Standard Library Documentation")).toBeInTheDocument()
    expect(screen.getByText("Bogleheads Wiki")).toBeInTheDocument()
  })

  it("displays size and artifact count for a multi-MB pack", async () => {
    renderStep()
    await waitFor(() => {
      expect(screen.getByText(/11\.\d+ MB|12(\.\d+)? MB/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/208 artifacts/i)).toBeInTheDocument()
  })

  it("renders a disabled Planned badge for a planned pack", async () => {
    renderStep()
    // Wait for registry to load
    await waitFor(() => {
      expect(screen.getByText("Bogleheads Wiki")).toBeInTheDocument()
    })
    // The planned pack's action button shows "Planned" and is disabled
    const plannedBtn = screen
      .getAllByRole("button")
      .find((b) => b.textContent?.trim() === "Planned")
    expect(plannedBtn).toBeDefined()
    expect(plannedBtn).toBeDisabled()
  })

  it("calls installKnowledgePack with the pack id when Install is clicked", async () => {
    const user = userEvent.setup()
    renderStep()

    await waitFor(() => {
      expect(screen.getByText("Python Standard Library Documentation")).toBeInTheDocument()
    })

    const installBtn = screen.getByRole("button", { name: /Install/i })
    await user.click(installBtn)

    await waitFor(() => {
      expect(installKnowledgePack).toHaveBeenCalledWith("python-stdlib-docs")
    })
  })

  it("calls onChange with updated installedPackIds and documentCount on install success", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderStep({ onChange })

    await waitFor(() => {
      expect(screen.getByText("Python Standard Library Documentation")).toBeInTheDocument()
    })

    await user.click(screen.getByRole("button", { name: /Install/i }))

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          installedPackIds: ["python-stdlib-docs"],
          firstDoc: expect.objectContaining({
            documentCount: 208,
          }),
        }),
      )
    })
  })

  it("is axe-clean", async () => {
    const { container } = renderStep()
    await waitFor(() => {
      expect(screen.getByText("Python Standard Library Documentation")).toBeInTheDocument()
    })
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
