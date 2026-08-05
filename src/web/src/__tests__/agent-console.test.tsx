// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
import type { AgentEvent } from "@/hooks/use-agent-console"
import { AgentConsole } from "@/components/console/AgentConsole"

// Emoji unicode ranges the console previously rendered inline (agent glyphs +
// the default "gear" fallback). Retrofit swaps these for lucide icons.
const EMOJI_PATTERN = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u

function makeEvent(overrides: Partial<AgentEvent> = {}): AgentEvent {
  return {
    id: "evt-1",
    agent: "query",
    message: "Resolved 3 sources",
    level: "success",
    timestamp: 1_700_000_000,
    metadata: {},
    ...overrides,
  }
}

function noop() {}

describe("AgentConsole — success state", () => {
  it("renders agent labels and messages for each event", () => {
    const events = [
      makeEvent({ id: "1", agent: "query", message: "Resolved 3 sources" }),
      makeEvent({ id: "2", agent: "decomposer", message: "Split into 2 sub-queries", level: "info" }),
      makeEvent({ id: "3", agent: "verification", message: "Entailment check passed", level: "success" }),
    ]
    render(<AgentConsole events={events} connected onClear={noop} onClose={noop} />)

    expect(screen.getByText("Resolved 3 sources")).toBeInTheDocument()
    expect(screen.getByText("Split into 2 sub-queries")).toBeInTheDocument()
    expect(screen.getByText("Entailment check passed")).toBeInTheDocument()
    expect(screen.getByText("Query")).toBeInTheDocument()
    expect(screen.getByText("Decomposer")).toBeInTheDocument()
    expect(screen.getByText("Verification")).toBeInTheDocument()
  })

  it("renders no emoji glyphs and at least one lucide icon svg", () => {
    const events = [
      makeEvent({ id: "1", agent: "query" }),
      makeEvent({ id: "2", agent: "unknown-agent", message: "fallback style" }),
    ]
    const { container } = render(
      <AgentConsole events={events} connected onClear={noop} onClose={noop} />,
    )

    expect(container.textContent).not.toMatch(EMOJI_PATTERN)
    expect(container.querySelectorAll("svg").length).toBeGreaterThan(0)
  })

  it("is axe-clean with events", async () => {
    const events = [makeEvent()]
    const { container } = render(
      <AgentConsole events={events} connected onClear={noop} onClose={noop} />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("AgentConsole — empty state (connected, idle)", () => {
  it("shows the idle empty state, not the loading state", () => {
    render(<AgentConsole events={[]} connected onClear={noop} onClose={noop} />)
    expect(screen.getByText(/No agent activity yet/i)).toBeInTheDocument()
    expect(screen.queryByText(/Connecting to agent stream/i)).not.toBeInTheDocument()
  })

  it("is axe-clean in the empty state", async () => {
    const { container } = render(
      <AgentConsole events={[]} connected onClear={noop} onClose={noop} />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("AgentConsole — loading state (not yet connected, no events)", () => {
  it("shows a connecting indicator, not the idle empty state", () => {
    render(<AgentConsole events={[]} connected={false} onClear={noop} onClose={noop} />)
    expect(screen.getByText(/Connecting to agent stream/i)).toBeInTheDocument()
    expect(screen.queryByText(/No agent activity yet/i)).not.toBeInTheDocument()
  })

  it("is axe-clean in the loading state", async () => {
    const { container } = render(
      <AgentConsole events={[]} connected={false} onClear={noop} onClose={noop} />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("AgentConsole — connection status accessibility", () => {
  it("exposes a role=status element naming 'reconnecting/disconnected' when disconnected", () => {
    const events = [makeEvent()]
    render(<AgentConsole events={events} connected={false} onClear={noop} onClose={noop} />)
    const status = screen.getByRole("status")
    expect(status.getAttribute("aria-label") ?? status.textContent).toMatch(
      /reconnecting|disconnected/i,
    )
  })

  it("exposes a role=status element naming 'live' when connected", () => {
    const events = [makeEvent()]
    render(<AgentConsole events={events} connected onClear={noop} onClose={noop} />)
    const status = screen.getByRole("status")
    expect(status.getAttribute("aria-label") ?? status.textContent).toMatch(/live/i)
  })

  it("is axe-clean when disconnected with events present", async () => {
    const events = [makeEvent()]
    const { container } = render(
      <AgentConsole events={events} connected={false} onClear={noop} onClose={noop} />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe("AgentConsole — filter chip", () => {
  it("shows the agent label with a lucide icon, no emoji, once filters are opened", () => {
    const events = [makeEvent({ agent: "query" })]
    render(<AgentConsole events={events} connected onClear={noop} onClose={noop} />)

    const filterButton = screen.getByRole("button", { name: "Filter agents" })
    fireEvent.click(filterButton)

    const chip = screen.getByRole("button", { name: "Query" })
    expect(chip.textContent).not.toMatch(EMOJI_PATTERN)
    expect(chip.querySelector("svg")).toBeTruthy()
  })
})
