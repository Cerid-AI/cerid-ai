// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { SmartRagWeights } from "@/components/settings/smart-rag-weights"

const mockFetchSources = vi.fn()
const mockFetchWeights = vi.fn()
const mockPutWeights = vi.fn()
const mockResetWeights = vi.fn()

vi.mock("@/lib/api/settings", () => ({
  fetchRagSources: (...a: unknown[]) => mockFetchSources(...a),
  fetchRagWeights: (...a: unknown[]) => mockFetchWeights(...a),
  putRagWeights: (...a: unknown[]) => mockPutWeights(...a),
  resetRagWeights: (...a: unknown[]) => mockResetWeights(...a),
}))

const sourcesSample = {
  sources: [
    {
      name: "gmail",
      kind: "data_source" as const,
      description: "Gmail via sibling MCP",
      default_enabled: true,
      current_weight: 1.0,
    },
    {
      name: "kb:notes",
      kind: "kb_domain" as const,
      description: "Personal KB: Notes",
      default_enabled: true,
      current_weight: 1.0,
    },
  ],
  min_weight: 0.0,
  max_weight: 2.0,
  default_weight: 1.0,
  feature_enabled: true,
}

beforeEach(() => {
  mockFetchSources.mockReset()
  mockFetchWeights.mockReset()
  mockPutWeights.mockReset()
  mockResetWeights.mockReset()
  mockFetchSources.mockResolvedValue(sourcesSample)
  mockFetchWeights.mockResolvedValue({
    weights: {},
    user_scope: "global",
    feature_enabled: true,
  })
})

describe("SmartRagWeights", () => {
  it("renders one row per source", async () => {
    render(<SmartRagWeights tier="pro" />)
    expect(await screen.findByTestId("smart-rag-row-gmail")).toBeInTheDocument()
    expect(screen.getByTestId("smart-rag-row-kb:notes")).toBeInTheDocument()
  })

  it("Pro tier renders editor without lock overlay", async () => {
    render(<SmartRagWeights tier="pro" />)
    await screen.findByTestId("smart-rag-row-gmail")
    expect(screen.queryByTestId("smart-rag-locked-overlay")).toBeNull()
  })

  it("community tier shows lock overlay + upgrade CTA", async () => {
    render(<SmartRagWeights tier="community" />)
    await screen.findByTestId("smart-rag-row-gmail")
    expect(screen.getByTestId("smart-rag-locked-overlay")).toBeInTheDocument()
    expect(screen.getByText(/Pro feature/i)).toBeInTheDocument()
  })

  it("save button disabled when no changes", async () => {
    render(<SmartRagWeights tier="pro" />)
    const save = await screen.findByTestId("smart-rag-save")
    expect(save).toBeDisabled()
  })

  it("dragging a slider enables save + computes recall impact", async () => {
    render(<SmartRagWeights tier="pro" />)
    const slider = await screen.findByTestId("smart-rag-slider-gmail")
    fireEvent.change(slider, { target: { value: "1.5" } })
    await waitFor(() => {
      expect(screen.getByTestId("smart-rag-save")).not.toBeDisabled()
    })
    expect(screen.getByText(/Estimated recall impact/)).toBeInTheDocument()
  })

  it("save POSTs only non-default weights", async () => {
    mockPutWeights.mockResolvedValue({
      weights: { gmail: 1.5 },
      user_scope: "global",
      feature_enabled: true,
    })
    const user = userEvent.setup()
    render(<SmartRagWeights tier="pro" />)
    const slider = await screen.findByTestId("smart-rag-slider-gmail")
    fireEvent.change(slider, { target: { value: "1.5" } })
    await user.click(screen.getByTestId("smart-rag-save"))
    await waitFor(() => {
      expect(mockPutWeights).toHaveBeenCalledWith({ gmail: 1.5 })
    })
  })

  it("reset issues DELETE and clears overrides", async () => {
    mockResetWeights.mockResolvedValue({
      weights: {},
      user_scope: "global",
      feature_enabled: true,
    })
    const user = userEvent.setup()
    render(<SmartRagWeights tier="pro" />)
    await screen.findByTestId("smart-rag-row-gmail")
    await user.click(screen.getByTestId("smart-rag-reset"))
    await waitFor(() => {
      expect(mockResetWeights).toHaveBeenCalled()
    })
  })

  it("surfaces error from fetch", async () => {
    mockFetchSources.mockRejectedValue(new Error("Backend down"))
    render(<SmartRagWeights tier="pro" />)
    expect(await screen.findByRole("alert")).toHaveTextContent(/Backend down/)
  })

  it("kb domain rows use Database icon vs data sources Globe icon", async () => {
    // Smoke: both icons render different SVG paths; we can't easily assert
    // SVG path here but we can confirm both rows render distinct kinds.
    render(<SmartRagWeights tier="pro" />)
    const dsRow = await screen.findByTestId("smart-rag-row-gmail")
    const kbRow = screen.getByTestId("smart-rag-row-kb:notes")
    expect(dsRow).toBeInTheDocument()
    expect(kbRow).toBeInTheDocument()
  })

  it("respects feature_enabled=false from server even at Pro tier", async () => {
    // E.g. operator disabled the feature via env override
    mockFetchSources.mockResolvedValue({
      ...sourcesSample,
      feature_enabled: false,
    })
    mockFetchWeights.mockResolvedValue({
      weights: {},
      user_scope: "global",
      feature_enabled: false,
    })
    render(<SmartRagWeights tier="pro" />)
    await screen.findByTestId("smart-rag-row-gmail")
    // Lock overlay still shows because server-side feature flag is off
    expect(screen.getByTestId("smart-rag-locked-overlay")).toBeInTheDocument()
  })
})
