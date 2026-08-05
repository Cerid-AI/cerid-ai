// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen } from "@testing-library/react"

// Mock the sentry module before importing the component so the spy
// captures the captureException dispatch.
vi.mock("@/lib/sentry", () => ({
  captureException: vi.fn(),
}))

describe("AppErrorBoundary", () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it("renders children when no error", async () => {
    const { AppErrorBoundary } = await import("@/components/layout/app-error-boundary")
    render(
      <AppErrorBoundary>
        <div>safe content</div>
      </AppErrorBoundary>,
    )
    expect(screen.getByText("safe content")).toBeInTheDocument()
  })

  it("forwards render errors to Sentry with componentStack", async () => {
    // Silence React's own error logging — getDerivedStateFromError still fires.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {})

    const { AppErrorBoundary } = await import("@/components/layout/app-error-boundary")
    const sentry = await import("@/lib/sentry")

    function Throws(): null {
      throw new Error("kaboom")
    }

    render(
      <AppErrorBoundary>
        <Throws />
      </AppErrorBoundary>,
    )

    // Fallback UI rendered
    expect(screen.getByText("Something went wrong")).toBeInTheDocument()

    // Sentry called with the error + componentStack extra
    expect(sentry.captureException).toHaveBeenCalledTimes(1)
    const [err, extra] = (sentry.captureException as ReturnType<typeof vi.fn>).mock.calls[0]
    expect((err as Error).message).toBe("kaboom")
    expect(extra).toHaveProperty("componentStack")

    consoleError.mockRestore()
  })
})
