// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * ImportDialog scan-preview rendering against the real
 * POST /admin/scan/preview contract.
 *
 * The endpoint's response_model is PreviewResponse — total_files,
 * total_size_mb, by_extension, by_domain — and pydantic drops everything
 * else. The preview pane must render what the server sends and must not
 * dereference the fields it does not.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  scanPreview: vi.fn(),
  startScan: vi.fn(),
  getScanProgress: vi.fn(),
}))

import { ImportDialog } from "@/components/kb/import-dialog"
import { scanPreview } from "@/lib/api"

/** Exactly the four keys PreviewResponse declares (scanner.py). */
const SERVER_PREVIEW = {
  total_files: 42,
  total_size_mb: 7.5,
  by_extension: { ".md": 30, ".pdf": 12 },
  by_domain: { general: 42 },
}

beforeEach(() => {
  vi.clearAllMocks()
})

async function scan() {
  render(<ImportDialog onClose={() => {}} />)
  fireEvent.click(screen.getByRole("button", { name: /^scan$/i }))
}

describe("ImportDialog — scan preview", () => {
  it("renders the preview from a PreviewResponse body without crashing", async () => {
    vi.mocked(scanPreview).mockResolvedValue(SERVER_PREVIEW as never)
    await scan()

    expect(await screen.findByText(/files to import/i)).toBeInTheDocument()
    expect(screen.getByText("42")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /import 42 files/i })).toBeInTheDocument()
  })

  it("omits the chunk/storage estimates the server does not send", async () => {
    vi.mocked(scanPreview).mockResolvedValue(SERVER_PREVIEW as never)
    await scan()
    await screen.findByText(/files to import/i)

    expect(screen.queryByText(/chunks/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/storage/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/skipping/i)).not.toBeInTheDocument()
  })

  it("shows the estimates when a backend does supply them", async () => {
    vi.mocked(scanPreview).mockResolvedValue({
      ...SERVER_PREVIEW,
      estimated_chunks: 1234,
      estimated_storage_mb: 3,
      skipped: { junk: 2, archives: 0, unsupported: 1, oversized: 0 },
    } as never)
    await scan()

    expect(await screen.findByText(/chunks/i)).toBeInTheDocument()
    expect(screen.getByText("~1,234")).toBeInTheDocument()
    expect(screen.getByText(/skipping 3 files/i)).toBeInTheDocument()
  })
})
