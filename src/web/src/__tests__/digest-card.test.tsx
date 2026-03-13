// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { DigestCard } from "@/components/monitoring/digest-card"
import type { DigestResponse } from "@/lib/types"

const makeDigest = (overrides: Partial<DigestResponse> = {}): DigestResponse => ({
  period_hours: 24,
  generated_at: "2026-03-13T12:00:00Z",
  artifacts: {
    count: 12,
    items: [
      {
        id: "a1",
        filename: "report.pdf",
        domain: "finance",
        summary: "Quarterly earnings report",
        ingested_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
      },
      {
        id: "a2",
        filename: "utils.py",
        domain: "coding",
        summary: "Utility functions",
        ingested_at: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
      },
    ],
    by_domain: { finance: 5, coding: 4, research: 3 },
  },
  relationships: { new_count: 8 },
  health: {
    status: "healthy",
    services: { chromadb: "connected", neo4j: "connected", redis: "connected" },
  },
  recent_events: 47,
  ...overrides,
})

describe("DigestCard", () => {
  it("renders 4 summary stats", () => {
    render(<DigestCard digest={makeDigest()} isLoading={false} />)
    // Artifacts count
    expect(screen.getByText("12")).toBeInTheDocument()
    expect(screen.getByText("Artifacts")).toBeInTheDocument()
    // Domains count (keys of by_domain)
    expect(screen.getByText("3")).toBeInTheDocument()
    expect(screen.getByText("Domains")).toBeInTheDocument()
    // Relationships count
    expect(screen.getByText("8")).toBeInTheDocument()
    expect(screen.getByText("Relationships")).toBeInTheDocument()
    // Events count
    expect(screen.getByText("47")).toBeInTheDocument()
    expect(screen.getByText("Events")).toBeInTheDocument()
  })

  it("renders domain breakdown badges", () => {
    render(<DigestCard digest={makeDigest()} isLoading={false} />)
    expect(screen.getByText(/finance/)).toBeInTheDocument()
    expect(screen.getByText(/coding/)).toBeInTheDocument()
    expect(screen.getByText(/research/)).toBeInTheDocument()
  })

  it("renders recent artifact filenames", () => {
    render(<DigestCard digest={makeDigest()} isLoading={false} />)
    expect(screen.getByText("report.pdf")).toBeInTheDocument()
    expect(screen.getByText("utils.py")).toBeInTheDocument()
  })

  it("shows empty state when no artifacts", () => {
    const empty = makeDigest({
      artifacts: { count: 0, items: [], by_domain: {} },
      relationships: { new_count: 0 },
      recent_events: 0,
    })
    render(<DigestCard digest={empty} isLoading={false} />)
    expect(screen.getByText(/no activity/i)).toBeInTheDocument()
  })

  it("shows loading state", () => {
    render(<DigestCard digest={undefined} isLoading={true} />)
    expect(screen.getByText(/loading digest/i)).toBeInTheDocument()
  })

  it("shows empty state when digest is undefined and not loading", () => {
    render(<DigestCard digest={undefined} isLoading={false} />)
    expect(screen.getByText(/no activity/i)).toBeInTheDocument()
  })

  it("calls onPeriodChange when period is changed", async () => {
    const user = userEvent.setup()
    const onPeriodChange = vi.fn()
    render(
      <DigestCard digest={makeDigest()} isLoading={false} onPeriodChange={onPeriodChange} />,
    )
    // Open the select dropdown
    const trigger = screen.getByRole("combobox")
    await user.click(trigger)
    // Select "Last 3 days"
    const option = screen.getByText("Last 3 days")
    await user.click(option)
    expect(onPeriodChange).toHaveBeenCalledWith(72)
  })

  it("limits recent artifacts list to 10 items", () => {
    const manyItems = Array.from({ length: 15 }, (_, i) => ({
      id: `a${i}`,
      filename: `file-${i}.txt`,
      domain: "coding",
      summary: `Summary ${i}`,
      ingested_at: new Date(Date.now() - i * 60 * 60 * 1000).toISOString(),
    }))
    const digest = makeDigest({
      artifacts: { count: 15, items: manyItems, by_domain: { coding: 15 } },
    })
    render(<DigestCard digest={digest} isLoading={false} />)
    // Only first 10 should render
    expect(screen.getByText("file-0.txt")).toBeInTheDocument()
    expect(screen.getByText("file-9.txt")).toBeInTheDocument()
    expect(screen.queryByText("file-10.txt")).not.toBeInTheDocument()
  })
})
