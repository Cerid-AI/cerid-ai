// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ModelSwitchDialog } from "@/components/chat/model-switch-dialog"
import type { ModelOption, ModelSwitchOptions } from "@/lib/types"

const targetModel: ModelOption = {
  id: "openrouter/openai/gpt-4o-mini",
  label: "GPT-4o Mini",
  provider: "openai",
  contextWindow: 128_000,
  inputCostPer1M: 0.15,
  outputCostPer1M: 0.60,
}

function makeOptions(overrides: Partial<ModelSwitchOptions> = {}): ModelSwitchOptions {
  return {
    targetModel,
    costEstimate: {
      replayCost: 0.0034,
      currentNextTurnCost: 0.005,
      summarizeCost: 0.0012,
      historyTokens: 2500,
      summarizedTokens: 250,
      exceedsTargetContext: false,
    },
    strategies: ["continue", "summarize", "fresh"],
    recommended: "continue",
    ...overrides,
  }
}

describe("ModelSwitchDialog", () => {
  it("renders all strategy buttons", () => {
    render(
      <ModelSwitchDialog
        options={makeOptions()}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText("Continue with full history")).toBeInTheDocument()
    expect(screen.getByText("Summarize and switch")).toBeInTheDocument()
    expect(screen.getByText("Start fresh")).toBeInTheDocument()
  })

  it("shows Recommended badge on the recommended strategy", () => {
    render(
      <ModelSwitchDialog
        options={makeOptions({ recommended: "summarize" })}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText("Recommended")).toBeInTheDocument()
  })

  it("shows history token count", () => {
    render(
      <ModelSwitchDialog
        options={makeOptions()}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText(/2,500 tokens/)).toBeInTheDocument()
  })

  it("shows Exceeds context badge when history exceeds target context", () => {
    render(
      <ModelSwitchDialog
        options={makeOptions({
          costEstimate: {
            replayCost: 0.01,
            currentNextTurnCost: 0.005,
            summarizeCost: 0.003,
            historyTokens: 120_000,
            summarizedTokens: 12_000,
            exceedsTargetContext: true,
          },
        })}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText("Exceeds context")).toBeInTheDocument()
  })

  it("disables Continue button when context is exceeded", async () => {
    render(
      <ModelSwitchDialog
        options={makeOptions({
          costEstimate: {
            replayCost: 0.01,
            currentNextTurnCost: 0.005,
            summarizeCost: 0.003,
            historyTokens: 120_000,
            summarizedTokens: 12_000,
            exceedsTargetContext: true,
          },
        })}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    const continueBtn = screen.getByText("Continue with full history").closest("button")
    expect(continueBtn).toBeDisabled()
  })

  it("shows Free label for Start fresh", () => {
    render(
      <ModelSwitchDialog
        options={makeOptions()}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText("Free")).toBeInTheDocument()
  })

  it("shows cost amounts for continue and summarize strategies", () => {
    render(
      <ModelSwitchDialog
        options={makeOptions()}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText("~$0.0034")).toBeInTheDocument()
    expect(screen.getByText("~$0.0012")).toBeInTheDocument()
  })

  it("calls onSelect with chosen strategy when clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()

    render(
      <ModelSwitchDialog
        options={makeOptions()}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={onSelect}
        onCancel={vi.fn()}
      />,
    )

    await user.click(screen.getByText("Start fresh").closest("button")!)
    expect(onSelect).toHaveBeenCalledWith("fresh")
  })

  it("calls onCancel when Cancel is clicked", async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()

    render(
      <ModelSwitchDialog
        options={makeOptions()}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={vi.fn()}
        onCancel={onCancel}
      />,
    )

    await user.click(screen.getByText("Cancel"))
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it("only shows available strategies (no summarize for short conversations)", () => {
    render(
      <ModelSwitchDialog
        options={makeOptions({ strategies: ["continue", "fresh"] })}
        currentModelId="openrouter/anthropic/claude-sonnet-4"
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText("Continue with full history")).toBeInTheDocument()
    expect(screen.getByText("Start fresh")).toBeInTheDocument()
    expect(screen.queryByText("Summarize and switch")).not.toBeInTheDocument()
  })
})
