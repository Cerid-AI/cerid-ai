// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { WhisperModelManager } from "@/components/settings/whisper-model-manager"

const mockList = vi.fn()
const mockStart = vi.fn()
const mockStatus = vi.fn()
const mockCancel = vi.fn()
const mockDelete = vi.fn()

vi.mock("@/lib/api/settings", () => ({
  fetchWhisperModels: (...a: unknown[]) => mockList(...a),
  startWhisperDownload: (...a: unknown[]) => mockStart(...a),
  getWhisperDownloadStatus: (...a: unknown[]) => mockStatus(...a),
  cancelWhisperDownload: (...a: unknown[]) => mockCancel(...a),
  deleteWhisperModel: (...a: unknown[]) => mockDelete(...a),
}))

const sampleList = {
  cache_dir: "/Users/test/.cerid/models/whisper",
  current_default: "medium-q5_0",
  models: [
    {
      id: "tiny",
      filename: "ggml-tiny.bin",
      size_mb: 75,
      rtf_estimate: 0.02,
      quality: "low",
      description: "Fastest; good for noisy or short recordings.",
      cached: false,
      cached_size_bytes: null,
    },
    {
      id: "medium-q5_0",
      filename: "ggml-medium-q5_0.bin",
      size_mb: 539,
      rtf_estimate: 0.15,
      quality: "high",
      description: "Quantized medium; ~⅓ the size with minimal accuracy loss.",
      cached: true,
      cached_size_bytes: 539 * 1024 * 1024,
    },
  ],
}

describe("WhisperModelManager", () => {
  beforeEach(() => {
    mockList.mockReset()
    mockStart.mockReset()
    mockStatus.mockReset()
    mockCancel.mockReset()
    mockDelete.mockReset()
    mockList.mockResolvedValue(sampleList)
  })

  it("renders all returned models with size + quality", async () => {
    render(<WhisperModelManager />)
    expect(await screen.findByText("tiny")).toBeInTheDocument()
    expect(screen.getByText("medium-q5_0")).toBeInTheDocument()
    expect(screen.getByText(/75 MB · low quality/)).toBeInTheDocument()
    expect(screen.getByText(/539 MB · high quality/)).toBeInTheDocument()
  })

  it("marks the current default model and shows 'cached' for cached models", async () => {
    render(<WhisperModelManager />)
    await screen.findByText("tiny")
    expect(screen.getByText("default")).toBeInTheDocument()
    expect(screen.getByText(/cached/i)).toBeInTheDocument()
  })

  it("shows Download button only for uncached models", async () => {
    render(<WhisperModelManager />)
    await screen.findByText("tiny")
    expect(screen.getByTestId("whisper-download-tiny")).toBeInTheDocument()
    expect(screen.queryByTestId("whisper-download-medium-q5_0")).toBeNull()
  })

  it("shows Delete button only for cached models", async () => {
    render(<WhisperModelManager />)
    await screen.findByText("tiny")
    expect(screen.queryByTestId("whisper-delete-tiny")).toBeNull()
    expect(screen.getByTestId("whisper-delete-medium-q5_0")).toBeInTheDocument()
  })

  it("clicking Download starts and polls for status", async () => {
    mockStart.mockResolvedValue({ download_id: "dl1", model_id: "tiny" })
    // Two polls: first "downloading", then "completed"
    mockStatus
      .mockResolvedValueOnce({
        download_id: "dl1",
        model_id: "tiny",
        state: "downloading",
        bytes_downloaded: 1024 * 1024,
        bytes_total: 75 * 1024 * 1024,
        error: null,
      })
      .mockResolvedValueOnce({
        download_id: "dl1",
        model_id: "tiny",
        state: "completed",
        bytes_downloaded: 75 * 1024 * 1024,
        bytes_total: 75 * 1024 * 1024,
        error: null,
      })

    const user = userEvent.setup()
    render(<WhisperModelManager />)
    await user.click(await screen.findByTestId("whisper-download-tiny"))

    await waitFor(() => {
      expect(mockStart).toHaveBeenCalledWith("tiny")
    })
  })

  it("clicking Delete on cached model calls API + refreshes", async () => {
    mockDelete.mockResolvedValue({ deleted: true })
    const user = userEvent.setup()
    render(<WhisperModelManager />)
    await user.click(await screen.findByTestId("whisper-delete-medium-q5_0"))
    await waitFor(() => {
      expect(mockDelete).toHaveBeenCalledWith("medium-q5_0")
      expect(mockList).toHaveBeenCalledTimes(2) // initial + refresh
    })
  })

  it("surfaces error from fetchWhisperModels", async () => {
    mockList.mockRejectedValue(new Error("Backend unreachable"))
    render(<WhisperModelManager />)
    expect(await screen.findByRole("alert")).toHaveTextContent(/Backend unreachable/)
  })
})
