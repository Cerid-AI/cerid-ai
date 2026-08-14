// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// RA-41 — /api/migrate/notion had no client; Notion users had no on-ramp.
// The wizard's pick step now offers a "Notion export" tile that runs a
// zip-upload + job-status-polling flow instead of createSource.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { SourceAddWizard } from "@/components/sources/source-add-wizard"

vi.mock("@/lib/api/sources", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/sources")>()),
  listSourceKinds: vi.fn(async () => []),
  createSource: vi.fn(),
}))

const migrateNotionExport = vi.fn()
const fetchMigrationStatus = vi.fn()

vi.mock("@/lib/api/migration", () => ({
  migrateNotionExport: (...args: unknown[]) => migrateNotionExport(...args),
  fetchMigrationStatus: (...args: unknown[]) => fetchMigrationStatus(...args),
}))

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SourceAddWizard open onClose={() => {}} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("SourceAddWizard — Notion migration", () => {
  it("offers a Notion export tile on the pick step", async () => {
    renderWizard()
    expect(await screen.findByRole("button", { name: /import a notion export/i })).toBeInTheDocument()
  })

  it("uploads the chosen zip and polls status through to completion", async () => {
    const user = userEvent.setup()
    migrateNotionExport.mockResolvedValue({ job_id: "job-1", pages_found: 3 })
    fetchMigrationStatus.mockResolvedValue({
      job_id: "job-1", status: "completed", total: 3, processed: 3, errors: 0,
    })

    renderWizard()
    await user.click(await screen.findByRole("button", { name: /import a notion export/i }))

    const fileInput = await screen.findByLabelText(/notion export \(\.zip\)/i)
    const file = new File(["zip-bytes"], "export.zip", { type: "application/zip" })
    await user.upload(fileInput, file)

    await user.click(screen.getByRole("button", { name: /^import$/i }))

    await waitFor(() => expect(migrateNotionExport).toHaveBeenCalledWith(file))
    await waitFor(() => expect(fetchMigrationStatus).toHaveBeenCalledWith("job-1"))
    expect(await screen.findByText(/import complete/i)).toBeInTheDocument()
    expect(screen.getByText(/3\/3 pages/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /^done$/i })).toBeEnabled()
  })

  it("keeps Done disabled while the job is still processing", async () => {
    const user = userEvent.setup()
    migrateNotionExport.mockResolvedValue({ job_id: "job-2", pages_found: 5 })
    fetchMigrationStatus.mockResolvedValue({
      job_id: "job-2", status: "processing", total: 5, processed: 2, errors: 0,
    })

    renderWizard()
    await user.click(await screen.findByRole("button", { name: /import a notion export/i }))
    const fileInput = await screen.findByLabelText(/notion export \(\.zip\)/i)
    await user.upload(fileInput, new File(["zip-bytes"], "export.zip", { type: "application/zip" }))
    await user.click(screen.getByRole("button", { name: /^import$/i }))

    expect(await screen.findByText(/2\/5 pages/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /^done$/i })).toBeDisabled()
  })
})
