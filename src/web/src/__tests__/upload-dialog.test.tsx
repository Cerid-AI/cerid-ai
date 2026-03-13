// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { UploadDialog } from "@/components/kb/upload-dialog"

/** Create a fake File with the given name and size in bytes. */
function fakeFile(name: string, size: number): File {
  const blob = new Blob(["x".repeat(size)], { type: "application/octet-stream" })
  return new File([blob], name, { type: "application/octet-stream" })
}

describe("UploadDialog", () => {
  describe("standard mode (≤2 files)", () => {
    it("renders title with single file", () => {
      render(
        <UploadDialog
          files={[fakeFile("readme.md", 1024)]}
          defaultDomain={null}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />,
      )
      expect(screen.getByRole("heading", { name: /Upload File/ })).toBeInTheDocument()
    })

    it("renders title with 2 files", () => {
      render(
        <UploadDialog
          files={[fakeFile("a.txt", 100), fakeFile("b.txt", 200)]}
          defaultDomain={null}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />,
      )
      expect(screen.getByRole("heading", { name: /Upload 2 Files/ })).toBeInTheDocument()
    })

    it("keeps simple mode for ≤2 files", () => {
      render(
        <UploadDialog
          files={[fakeFile("a.txt", 100), fakeFile("b.txt", 200)]}
          defaultDomain={null}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />,
      )
      // Standard mode shows "Upload 2 Files" button, NOT "Start Batch"
      expect(screen.getByRole("button", { name: /Upload 2 Files/ })).toBeInTheDocument()
      expect(screen.queryByText(/Start Batch/)).not.toBeInTheDocument()
    })
  })

  describe("batch mode", () => {
    const batchFiles = [
      fakeFile("report.pdf", 2_500_000),
      fakeFile("notes.md", 1_200),
      fakeFile("data.csv", 50_000),
    ]

    it("shows batch header with file count when ≥3 files", () => {
      render(
        <UploadDialog
          files={batchFiles}
          defaultDomain={null}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />,
      )
      // Batch header should contain "Batch Upload" and the file count
      expect(screen.getByText(/Batch Upload — 3 Files/)).toBeInTheDocument()
    })

    it("renders per-file rows with filenames", () => {
      render(
        <UploadDialog
          files={batchFiles}
          defaultDomain={null}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />,
      )
      expect(screen.getByText("report.pdf")).toBeInTheDocument()
      expect(screen.getByText("notes.md")).toBeInTheDocument()
      expect(screen.getByText("data.csv")).toBeInTheDocument()
    })

    it("shows total size", () => {
      render(
        <UploadDialog
          files={batchFiles}
          defaultDomain={null}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />,
      )
      // Total of ~2.55 MB displayed as "2.4 MB" (formatFileSize rounds)
      expect(screen.getByText(/Total:/)).toBeInTheDocument()
    })

    it("calls onConfirm with options when upload button clicked", () => {
      const onConfirm = vi.fn()
      render(
        <UploadDialog
          files={batchFiles}
          defaultDomain={null}
          onConfirm={onConfirm}
          onCancel={vi.fn()}
        />,
      )
      fireEvent.click(screen.getByRole("button", { name: /Start Batch/ }))
      expect(onConfirm).toHaveBeenCalledTimes(1)
      expect(onConfirm).toHaveBeenCalledWith(
        expect.objectContaining({ categorize_mode: "smart" }),
      )
    })

    it("shows Start Batch button for ≥3 files", () => {
      render(
        <UploadDialog
          files={[...batchFiles, fakeFile("extra.txt", 500)]}
          defaultDomain={null}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
        />,
      )
      expect(screen.getByRole("button", { name: /Start Batch/ })).toBeInTheDocument()
    })
  })
})
