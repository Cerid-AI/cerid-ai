// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"

function ThrowOnFirstRender({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) throw new Error("t.map is not a function")
  return <div>recovered</div>
}

describe("PaneErrorBoundary", () => {
  it("renders Retry button on error", () => {
    const qc = new QueryClient()
    render(
      <QueryClientProvider client={qc}>
        <PaneErrorBoundary label="test-pane">
          <ThrowOnFirstRender shouldThrow={true} />
        </PaneErrorBoundary>
      </QueryClientProvider>,
    )
    expect(screen.getByText(/test-pane failed to render/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
  })

  it("calls queryClient.invalidateQueries() on Retry click", () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries")
    render(
      <QueryClientProvider client={qc}>
        <PaneErrorBoundary label="test-pane" queryClient={qc}>
          <ThrowOnFirstRender shouldThrow={true} />
        </PaneErrorBoundary>
      </QueryClientProvider>,
    )
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))
    expect(invalidateSpy).toHaveBeenCalledTimes(1)
  })
})
