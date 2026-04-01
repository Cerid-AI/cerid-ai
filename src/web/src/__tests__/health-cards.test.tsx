// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { HealthCards } from "@/components/monitoring/health-cards"
import type { MaintenanceHealth } from "@/lib/types"

const makeHealth = (overrides: Partial<MaintenanceHealth> = {}): MaintenanceHealth => ({
  overall: "healthy",
  services: {
    chromadb: "connected",
    redis: "connected",
    neo4j: "connected",
  },
  data: {
    collections: 6,
    total_chunks: 1500,
    collection_sizes: { kb_coding: 500, kb_finance: 300 },
    artifacts: 42,
    domains: 6,
    audit_log_entries: 200,
  },
  ...overrides,
})

describe("HealthCards", () => {
  it("renders all service cards when data is provided", () => {
    render(<HealthCards health={makeHealth()} />)
    expect(screen.getByText("ChromaDB")).toBeInTheDocument()
    expect(screen.getByText("Neo4j")).toBeInTheDocument()
    expect(screen.getByText("Redis")).toBeInTheDocument()
  })

  it("shows connected status for healthy services", () => {
    render(<HealthCards health={makeHealth()} />)
    const connectedBadges = screen.getAllByText("connected")
    expect(connectedBadges.length).toBeGreaterThanOrEqual(3)
  })

  it("shows data counts for ChromaDB", () => {
    render(<HealthCards health={makeHealth()} />)
    // total_chunks.toLocaleString() → "1,500 chunks"
    expect(screen.getByText(/1,500/)).toBeInTheDocument()
  })

  it("shows artifact count for Neo4j", () => {
    render(<HealthCards health={makeHealth()} />)
    // "42 artifacts" rendered as full text in a <p> tag
    expect(screen.getByText(/42 artifacts/)).toBeInTheDocument()
  })

  it("shows error state for failed service", () => {
    const health = makeHealth({
      overall: "degraded",
      services: {
        chromadb: "connected",
        redis: "error: connection refused",
        neo4j: "connected",
      },
    })
    render(<HealthCards health={health} />)
    expect(screen.getByText(/error/i)).toBeInTheDocument()
  })

  it("renders nothing when health is undefined", () => {
    const { container } = render(<HealthCards health={undefined} />)
    // Returns null — container should have no rendered child elements
    expect(container.firstChild).toBeNull()
  })

  it("shows skipped status for bifrost", () => {
    const health = makeHealth({
      services: {
        chromadb: "connected",
        redis: "connected",
        neo4j: "connected",
        bifrost: "skipped (sync context)",
      },
    })
    render(<HealthCards health={health} />)
    expect(screen.getByText(/skipped/i)).toBeInTheDocument()
  })
})
