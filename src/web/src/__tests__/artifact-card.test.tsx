// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ArtifactCard } from "@/components/kb/artifact-card"
import type { KBQueryResult } from "@/lib/types"

const makeResult = (overrides: Partial<KBQueryResult> = {}): KBQueryResult => ({
  content: "This is some test content from the knowledge base.",
  relevance: 0.85,
  artifact_id: "art-1",
  filename: "test-document.py",
  domain: "coding",
  chunk_index: 0,
  collection: "kb_coding",
  ingested_at: "2026-01-15T10:00:00Z",
  ...overrides,
})

describe("ArtifactCard", () => {
  it("renders filename and domain badge", () => {
    render(
      <ArtifactCard result={makeResult()} isSelected={false} onSelect={vi.fn()} onInject={vi.fn()} />,
    )
    expect(screen.getByText("test-document.py")).toBeInTheDocument()
    // Domain is rendered via DomainBadge which outputs the domain name
    expect(screen.getByText("coding")).toBeInTheDocument()
  })

  it("shows relevance percentage", () => {
    render(
      <ArtifactCard result={makeResult({ relevance: 0.92 })} isSelected={false} onSelect={vi.fn()} onInject={vi.fn()} />,
    )
    expect(screen.getByText("92%")).toBeInTheDocument()
  })

  it("shows sub-category when present and not general", () => {
    render(
      <ArtifactCard
        result={makeResult({ sub_category: "python" })}
        isSelected={false}
        onSelect={vi.fn()}
        onInject={vi.fn()}
      />,
    )
    expect(screen.getByText("python")).toBeInTheDocument()
  })

  it("shows tags when present", () => {
    render(
      <ArtifactCard
        result={makeResult({ tags: ["fastapi", "auth", "middleware"] })}
        isSelected={false}
        onSelect={vi.fn()}
        onInject={vi.fn()}
      />,
    )
    expect(screen.getByText("fastapi")).toBeInTheDocument()
    expect(screen.getByText("auth")).toBeInTheDocument()
    expect(screen.getByText("middleware")).toBeInTheDocument()
  })

  it("shows quality badge with Q-score format for excellent scores", () => {
    render(
      <ArtifactCard
        result={makeResult({ quality_score: 0.9 })}
        isSelected={false}
        onSelect={vi.fn()}
        onInject={vi.fn()}
      />,
    )
    // QualityBadge renders "Q{pct}" format (e.g., "Q90")
    expect(screen.getByText("Q90")).toBeInTheDocument()
  })

  it("shows quality badge with Q-score format for good scores", () => {
    render(
      <ArtifactCard
        result={makeResult({ quality_score: 0.7 })}
        isSelected={false}
        onSelect={vi.fn()}
        onInject={vi.fn()}
      />,
    )
    expect(screen.getByText("Q70")).toBeInTheDocument()
  })

  it("shows graph source indicator", () => {
    render(
      <ArtifactCard
        result={makeResult({ graph_source: true, relationship_type: "RELATED_TO" })}
        isSelected={false}
        onSelect={vi.fn()}
        onInject={vi.fn()}
      />,
    )
    expect(screen.getByText(/graph/i)).toBeInTheDocument()
  })

  it("shows cross-domain indicator", () => {
    render(
      <ArtifactCard
        result={makeResult({ cross_domain: true })}
        isSelected={false}
        onSelect={vi.fn()}
        onInject={vi.fn()}
      />,
    )
    expect(screen.getByText(/cross/i)).toBeInTheDocument()
  })

  it("calls onSelect when card is clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <ArtifactCard result={makeResult()} isSelected={false} onSelect={onSelect} onInject={vi.fn()} />,
    )
    await user.click(screen.getByText("test-document.py"))
    expect(onSelect).toHaveBeenCalled()
  })

  it("shows content preview", () => {
    render(
      <ArtifactCard result={makeResult()} isSelected={false} onSelect={vi.fn()} onInject={vi.fn()} />,
    )
    expect(screen.getByText(/test content from the knowledge base/)).toBeInTheDocument()
  })

  it("does not show quality badge when no score", () => {
    render(
      <ArtifactCard result={makeResult()} isSelected={false} onSelect={vi.fn()} onInject={vi.fn()} />,
    )
    // No quality_score → no QualityBadge rendered
    expect(screen.queryByText(/^Q\d+$/)).not.toBeInTheDocument()
  })
})
