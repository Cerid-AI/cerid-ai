// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { HFTokenStep } from "@/components/setup/hf-token-step"

const mockFetchStatus = vi.fn()
const mockPut = vi.fn()
const mockTest = vi.fn()

vi.mock("@/lib/api/settings", () => ({
  fetchHFTokenStatus: (...args: unknown[]) => mockFetchStatus(...args),
  putHFToken: (...args: unknown[]) => mockPut(...args),
  testHFToken: (...args: unknown[]) => mockTest(...args),
}))

describe("HFTokenStep", () => {
  beforeEach(() => {
    mockFetchStatus.mockReset()
    mockPut.mockReset()
    mockTest.mockReset()
    mockFetchStatus.mockResolvedValue({
      configured: false,
      last4: null,
      updated_at: null,
      model_access: null,
    })
  })

  it("renders both gated-model links", async () => {
    render(<HFTokenStep />)
    expect(await screen.findByText(/Speaker Diarization 3.1/)).toBeInTheDocument()
    expect(screen.getByText(/Segmentation 3.0/)).toBeInTheDocument()
  })

  it("links to huggingface.co/settings/tokens", async () => {
    render(<HFTokenStep />)
    const link = await screen.findByRole("link", { name: /Create a token at huggingface.co/i })
    expect(link.getAttribute("href")).toBe("https://huggingface.co/settings/tokens")
  })

  it("save button disabled when input empty", async () => {
    render(<HFTokenStep />)
    const saveBtn = await screen.findByTestId("hf-token-save")
    expect(saveBtn).toBeDisabled()
  })

  it("saves token + tests gated access + calls onComplete when all accepted", async () => {
    mockPut.mockResolvedValue({
      configured: true,
      last4: "wxyz",
      updated_at: "2026-05-21T00:00:00Z",
      model_access: null,
    })
    mockTest.mockResolvedValue({
      valid: true,
      gated_model_access: {
        "pyannote/speaker-diarization-3.1": true,
        "pyannote/segmentation-3.0": true,
      },
      error: null,
    })
    const onComplete = vi.fn()
    const user = userEvent.setup()

    render(<HFTokenStep onComplete={onComplete} />)
    const input = await screen.findByLabelText(/Paste your token/i)
    await user.type(input, "hf_test1234567890abcdwxyz")  // pragma: allowlist secret  // pragma: allowlist secret
    await user.click(screen.getByTestId("hf-token-save"))

    await waitFor(() => {
      expect(mockPut).toHaveBeenCalledWith("hf_test1234567890abcdwxyz")  // pragma: allowlist secret
      expect(mockTest).toHaveBeenCalledWith("hf_test1234567890abcdwxyz")  // pragma: allowlist secret
      expect(onComplete).toHaveBeenCalled()
    })
    expect(await screen.findByText(/Token valid; all gated models accepted/i))
      .toBeInTheDocument()
  })

  it("does not call onComplete when token valid but a gate is unaccepted", async () => {
    mockPut.mockResolvedValue({
      configured: true,
      last4: "wxyz",
      updated_at: "2026-05-21T00:00:00Z",
      model_access: null,
    })
    mockTest.mockResolvedValue({
      valid: true,
      gated_model_access: {
        "pyannote/speaker-diarization-3.1": false,
        "pyannote/segmentation-3.0": true,
      },
      error: null,
    })
    const onComplete = vi.fn()
    const user = userEvent.setup()

    render(<HFTokenStep onComplete={onComplete} />)
    const input = await screen.findByLabelText(/Paste your token/i)
    await user.type(input, "hf_test1234567890abcdwxyz")  // pragma: allowlist secret
    await user.click(screen.getByTestId("hf-token-save"))

    await waitFor(() => {
      expect(mockTest).toHaveBeenCalled()
    })
    expect(onComplete).not.toHaveBeenCalled()
    expect(await screen.findByText(/at least one model license still needs accepting/i))
      .toBeInTheDocument()
  })

  it("shows test-stored button when already configured", async () => {
    mockFetchStatus.mockResolvedValue({
      configured: true,
      last4: "wxyz",
      updated_at: "2026-05-21T00:00:00Z",
      model_access: null,
    })
    render(<HFTokenStep />)
    expect(await screen.findByTestId("hf-token-test-stored")).toBeInTheDocument()
    expect(screen.getByText(/…wxyz/)).toBeInTheDocument()
  })

  it("surfaces invalid-token error", async () => {
    mockPut.mockResolvedValue({
      configured: true,
      last4: "wxyz",
      updated_at: "2026-05-21T00:00:00Z",
      model_access: null,
    })
    mockTest.mockResolvedValue({
      valid: false,
      gated_model_access: null,
      error: "Invalid token (401)",
    })
    const user = userEvent.setup()

    render(<HFTokenStep />)
    const input = await screen.findByLabelText(/Paste your token/i)
    await user.type(input, "hf_test1234567890abcdwxyz")  // pragma: allowlist secret
    await user.click(screen.getByTestId("hf-token-save"))

    expect(await screen.findByRole("alert")).toHaveTextContent(/Invalid token/i)
  })
})

describe("HFTokenStep — axe-clean", () => {
  it("is axe-clean in the default (not configured) state", async () => {
    const { container } = render(<HFTokenStep />)
    await screen.findByTestId("hf-token-save")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean when a token is already configured", async () => {
    mockFetchStatus.mockResolvedValue({
      configured: true,
      last4: "wxyz",
      updated_at: "2026-05-21T00:00:00Z",
      model_access: null,
    })
    const { container } = render(<HFTokenStep />)
    await screen.findByTestId("hf-token-test-stored")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in the invalid-token error state", async () => {
    mockPut.mockResolvedValue({
      configured: true,
      last4: "wxyz",
      updated_at: "2026-05-21T00:00:00Z",
      model_access: null,
    })
    mockTest.mockResolvedValue({
      valid: false,
      gated_model_access: null,
      error: "Invalid token (401)",
    })
    const user = userEvent.setup()
    const { container } = render(<HFTokenStep />)
    const input = await screen.findByLabelText(/Paste your token/i)
    await user.type(input, "hf_test1234567890abcdwxyz")  // pragma: allowlist secret
    await user.click(screen.getByTestId("hf-token-save"))
    await screen.findByRole("alert")
    expect(await axe(container)).toHaveNoViolations()
  })
})
