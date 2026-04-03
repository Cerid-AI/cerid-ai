// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  uploadFile: vi.fn().mockResolvedValue({ artifact_id: "test-1", filename: "test.pdf" }),
  queryKB: vi.fn().mockResolvedValue({ response: "Test response" }),
}))

vi.mock("@/hooks/use-drag-drop", () => ({
  useDragDrop: () => ({
    isDragOver: false,
    dragHandlers: {
      onDragEnter: vi.fn(),
      onDragLeave: vi.fn(),
      onDragOver: vi.fn(),
      onDrop: vi.fn(),
    },
  }),
}))

import { FirstDocumentStep } from "@/components/setup/first-document-step"

const DEFAULT_STATE = {
  ingested: false,
  queried: false,
  skipped: false,
}

interface FirstDocState {
  ingested: boolean
  queried: boolean
  skipped: boolean
}

const onChange = vi.fn<(state: FirstDocState) => void>()

beforeEach(() => {
  vi.restoreAllMocks()
  onChange.mockClear()
})

describe("FirstDocumentStep", () => {
  it("shows 'Try It Out' heading", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("Try It Out")).toBeInTheDocument()
  })

  it("shows upload zone with drop instruction", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("Drop a file or click to upload")).toBeInTheDocument()
  })

  it("shows 'Use sample content' button", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("Use sample content")).toBeInTheDocument()
  })

  it("shows supported file type info", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("PDF, TXT, MD, DOCX")).toBeInTheDocument()
  })

  it("has a hidden file input with correct accept types", () => {
    const { container } = render(
      <FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />,
    )
    const fileInput = container.querySelector("input[type='file']")
    expect(fileInput).toBeInTheDocument()
    expect(fileInput).toHaveAttribute("accept", ".pdf,.txt,.md,.docx")
  })

  it("shows Quick start badge next to sample content button", () => {
    render(<FirstDocumentStep state={DEFAULT_STATE} onChange={onChange} />)
    expect(screen.getByText("Quick start")).toBeInTheDocument()
  })
})
