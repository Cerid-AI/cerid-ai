// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
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
    overall: "healthy",
    services: { chromadb: "connected", neo4j: "connected", redis: "connected" },
    data: { collections: 5, total_chunks: 100, collection_sizes: {}, artifacts: 12, domains: 3, audit_log_entries: 47 },
  },
  recent_events: 47,
  ...overrides,
})

describe("DigestCard", () => {
  it("renders 5 summary stats including Errors", () => {
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
    // Errors count (0 when no errors field)
    expect(screen.getByText("Errors")).toBeInTheDocument()
  })

  it("renders domain breakdown badges", () => {
    render(<DigestCard digest={makeDigest()} isLoading={false} />)
    // Badges render as "count domain" e.g. "5 finance"
    expect(screen.getByText("5 finance")).toBeInTheDocument()
    expect(screen.getByText("4 coding")).toBeInTheDocument()
    expect(screen.getByText("3 research")).toBeInTheDocument()
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

  it("renders period selector", () => {
    const onPeriodChange = vi.fn()
    render(
      <DigestCard digest={makeDigest()} isLoading={false} onPeriodChange={onPeriodChange} />,
    )
    // Radix Select renders a combobox trigger
    expect(screen.getByRole("combobox")).toBeInTheDocument()
  })

  it("renders Errors button when errors.count > 0", () => {
    const digest = makeDigest({
      errors: {
        count: 14,
        items: [
          { timestamp: new Date().toISOString(), filename: "bad.pdf", domain: "finance", detail: "Parse failed: malformed PDF header" },
        ],
      },
    })
    render(<DigestCard digest={digest} isLoading={false} />)
    const button = screen.getByRole("button", { name: /view 14 ingestion errors/i })
    expect(button).toBeInTheDocument()
    expect(button).toHaveTextContent("14")
  })

  it("opens error drill-through dialog when Errors button clicked", () => {
    const digest = makeDigest({
      errors: {
        count: 2,
        items: [
          { timestamp: new Date(Date.now() - 3 * 60_000).toISOString(), filename: "doc1.pdf", domain: "research", detail: "Parse failed" },
          { timestamp: new Date(Date.now() - 10 * 60_000).toISOString(), filename: "doc2.epub", domain: "coding", detail: "Unsupported encoding" },
        ],
      },
    })
    render(<DigestCard digest={digest} isLoading={false} />)
    fireEvent.click(screen.getByRole("button", { name: /view 2 ingestion errors/i }))
    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByText("doc1.pdf")).toBeInTheDocument()
    expect(screen.getByText("doc2.epub")).toBeInTheDocument()
    expect(screen.getByText(/parse failed/i)).toBeInTheDocument()
    expect(screen.getByText(/unsupported encoding/i)).toBeInTheDocument()
  })

  it("renders Errors stat as non-interactive when count is 0", () => {
    render(<DigestCard digest={makeDigest()} isLoading={false} />)
    // Errors label exists, but there's no button role for it (0 count → plain div)
    expect(screen.queryByRole("button", { name: /ingestion errors/i })).not.toBeInTheDocument()
    expect(screen.getByText("Errors")).toBeInTheDocument()
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
