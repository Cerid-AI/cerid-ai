// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { DegradedBanner } from "@/components/chat/degraded-banner"

describe("DegradedBanner", () => {
  it("renders null when reason is empty", () => {
    const { container } = render(<DegradedBanner reason="" />)
    expect(container.firstChild).toBeNull()
  })

  it("shows the reason and a short label", () => {
    render(<DegradedBanner reason="Retrieval took longer than the configured budget." />)
    expect(screen.getByText(/retrieval budget exceeded/i)).toBeInTheDocument()
    expect(screen.getByText(/longer than the configured budget/i)).toBeInTheDocument()
  })

  it("does not label a pool-wait as a budget miss", () => {
    render(
      <DegradedBanner reason="Retrieval is queued behind other knowledge queries. Retry in a moment." />,
    )
    expect(screen.queryByText(/retrieval budget exceeded/i)).toBeNull()
    expect(screen.getByText(/waiting on other knowledge queries/i)).toBeInTheDocument()
  })
})
