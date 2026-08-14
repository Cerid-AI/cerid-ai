// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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
}))

// Wizard surfaces install through the async contract helper in lib/api/setup;
// resolving "installed" simulates a legacy synchronous backend (no polling).
vi.mock("@/lib/api/setup", () => ({
  startPackInstall: vi.fn().mockResolvedValue({ status: "installed", jobId: null }),
}))

import { BuildKnowledgeStep } from "@/components/setup/build-knowledge-step"
import { fetchKnowledgePackRegistry } from "@/lib/api/knowledge-packs"
import { startPackInstall } from "@/lib/api/setup"

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

  it("renders an error state with Retry when the registry fetch fails — not the empty-registry copy (WB-13)", async () => {
    vi.mocked(fetchKnowledgePackRegistry).mockRejectedValueOnce(new Error("stack still starting"))
    renderStep()

    expect(await screen.findByText(/couldn't load the pack registry/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
    expect(screen.queryByText(/no packs in registry/i)).not.toBeInTheDocument()
  })

  it("recovers from a failed registry fetch when Retry is clicked", async () => {
    vi.mocked(fetchKnowledgePackRegistry).mockRejectedValueOnce(new Error("stack still starting"))
    const user = userEvent.setup()
    renderStep()

    await user.click(await screen.findByRole("button", { name: /retry/i }))

    expect(await screen.findByText("Python Standard Library Documentation")).toBeInTheDocument()
    expect(screen.queryByText(/couldn't load the pack registry/i)).not.toBeInTheDocument()
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

  it("calls startPackInstall with the pack id when Install is clicked", async () => {
    const user = userEvent.setup()
    renderStep()

    await waitFor(() => {
      expect(screen.getByText("Python Standard Library Documentation")).toBeInTheDocument()
    })

    const installBtn = screen.getByRole("button", { name: /Install/i })
    await user.click(installBtn)

    await waitFor(() => {
      expect(startPackInstall).toHaveBeenCalledWith("python-stdlib-docs")
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

  // ---- Card states from registry install flags (beta triage P0-A #3) ----

  it("renders an Installed badge instead of an Install button when the registry reports installed", async () => {
    vi.mocked(fetchKnowledgePackRegistry).mockResolvedValueOnce({
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
            size_bytes: 12_345_678,
            artifact_count: 208,
            download_url: "https://example.com/pystd.tar.gz",
            sha256: "abc123",
            provenance: { status: "built" },
            installed: true,
            installing: false,
          } as never,
        ],
      },
    })
    renderStep()

    await waitFor(() => {
      expect(screen.getByText("Python Standard Library Documentation")).toBeInTheDocument()
    })
    expect(screen.getAllByText(/Installed|Done/).length).toBeGreaterThanOrEqual(1)
    expect(
      screen.queryByRole("button", { name: /Install Python Standard Library Documentation/i }),
    ).not.toBeInTheDocument()
  })

  it("disables the Install button and shows Installing while the registry reports installing", async () => {
    vi.mocked(fetchKnowledgePackRegistry).mockResolvedValueOnce({
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
            size_bytes: 12_345_678,
            artifact_count: 208,
            download_url: "https://example.com/pystd.tar.gz",
            sha256: "abc123",
            provenance: { status: "built" },
            installed: false,
            installing: true,
          } as never,
        ],
      },
    })
    renderStep()

    await waitFor(() => {
      expect(screen.getByText("Python Standard Library Documentation")).toBeInTheDocument()
    })
    const busyBtn = screen.getByRole("button", {
      name: /Install Python Standard Library Documentation/i,
    })
    expect(busyBtn).toBeDisabled()
    expect(busyBtn).toHaveTextContent(/Installing/i)
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
