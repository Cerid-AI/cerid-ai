// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests for ModelDownloadBanner (Workstream E Phase E.6.6).

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import {
  ModelDownloadBanner,
  _resetDismissedForTest,
} from "@/components/model-download-banner"
import * as settingsApi from "@/lib/api/settings"

// Stub sonner so toast.success/error don't render into the DOM during tests.
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(async () => {
  vi.restoreAllMocks()
  _resetDismissedForTest()
  // The sonner mock is hoisted at module load; the spies persist across
  // tests, so explicitly clear their call history per case.
  const { toast } = await import("sonner")
  ;(toast.success as ReturnType<typeof vi.fn>).mockClear()
  ;(toast.error as ReturnType<typeof vi.fn>).mockClear()
})

describe("ModelDownloadBanner", () => {
  it("renders nothing when both models are cached and never were uncached", async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: true, files: {}, loading: false },
      embedder: { repo: "e", cached: true, files: {}, loading: false },
    })

    const { container } = render(<ModelDownloadBanner />, { wrapper })
    // Wait for the query to resolve, then confirm no banner / no alert.
    await waitFor(() =>
      expect(settingsApi.fetchModelsStatus).toHaveBeenCalled(),
    )
    // Banner returns null when cached → container is essentially empty
    // (sonner's mocked toast didn't fire either).
    expect(container.querySelector("[role=alert]")).toBeNull()
    expect(container.querySelector("[role=status]")).toBeNull()
  })

  it("shows the proactive uncached banner with a download button", async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: false, files: {}, loading: false },
      embedder: { repo: "e", cached: false, files: {}, loading: false },
    })

    render(<ModelDownloadBanner />, { wrapper })
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /First semantic query will trigger model download/i,
      ),
    )
    expect(
      screen.getByRole("button", { name: /download now/i }),
    ).toBeInTheDocument()
  })

  it("renders the loading variant when server reports loading=true", async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: false, files: {}, loading: true },
      embedder: { repo: "e", cached: false, files: {}, loading: false },
    })

    render(<ModelDownloadBanner />, { wrapper })
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        /Downloading inference models/i,
      ),
    )
    // Loading variant doesn't expose the alert role
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("hides the banner permanently when user clicks Dismiss", async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: false, files: {}, loading: false },
      embedder: { repo: "e", cached: false, files: {}, loading: false },
    })

    const user = userEvent.setup()
    render(<ModelDownloadBanner />, { wrapper })

    const dismiss = await screen.findByRole("button", {
      name: /dismiss model download/i,
    })
    await user.click(dismiss)
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull())
    // localStorage flag set
    expect(window.localStorage.getItem("cerid-model-download-banner-dismissed"))
      .toBe("true")
  })

  it("calls preloadModels and refetches status when Download Now is clicked",
     async () => {
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: false, files: {}, loading: false },
      embedder: { repo: "e", cached: false, files: {}, loading: false },
    })
    const preloadSpy = vi.spyOn(settingsApi, "preloadModels").mockResolvedValue({
      status: "ok",
      reranker_status: "loaded",
      reranker_ms: 8000,
      embedder_status: "loaded",
      embedder_ms: 12000,
      total_ms: 20000,
    })

    const user = userEvent.setup()
    render(<ModelDownloadBanner />, { wrapper })

    const button = await screen.findByRole("button", { name: /download now/i })
    await user.click(button)

    await waitFor(() => expect(preloadSpy).toHaveBeenCalledTimes(1))
  })

  it("fires success toast on cached transition after observing uncached state",
     async () => {
    const { toast } = await import("sonner")

    // First fetch: not cached. Second fetch (after the spy is updated):
    // cached. The banner's polling/refetch picks up the transition.
    const fetchSpy = vi.spyOn(settingsApi, "fetchModelsStatus")
      .mockResolvedValueOnce({
        reranker: { repo: "r", cached: false, files: {}, loading: false },
        embedder: { repo: "e", cached: false, files: {}, loading: false },
      })
      .mockResolvedValueOnce({
        reranker: { repo: "r", cached: true, files: {}, loading: false },
        embedder: { repo: "e", cached: true, files: {}, loading: false },
      })

    // Trigger preload to drive the refetch path
    vi.spyOn(settingsApi, "preloadModels").mockResolvedValue({
      status: "ok",
      reranker_status: "loaded",
      reranker_ms: 8000,
      embedder_status: "loaded",
      embedder_ms: 12000,
      total_ms: 20000,
    })

    const user = userEvent.setup()
    render(<ModelDownloadBanner />, { wrapper })

    const button = await screen.findByRole("button", { name: /download now/i })
    await act(async () => {
      await user.click(button)
    })

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Inference models ready",
        expect.objectContaining({
          description: expect.stringContaining("full speed"),
        }),
      ),
    )
  })

  it("does NOT fire success toast when models were cached at mount", async () => {
    const { toast } = await import("sonner")
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: true, files: {}, loading: false },
      embedder: { repo: "e", cached: true, files: {}, loading: false },
    })

    render(<ModelDownloadBanner />, { wrapper })
    await waitFor(() =>
      expect(settingsApi.fetchModelsStatus).toHaveBeenCalled(),
    )
    // Verify no success toast was emitted on the cached-at-mount path
    expect(toast.success).not.toHaveBeenCalled()
  })

  it("renders error toast when preload fails but keeps banner visible",
     async () => {
    const { toast } = await import("sonner")
    vi.spyOn(settingsApi, "fetchModelsStatus").mockResolvedValue({
      reranker: { repo: "r", cached: false, files: {}, loading: false },
      embedder: { repo: "e", cached: false, files: {}, loading: false },
    })
    vi.spyOn(settingsApi, "preloadModels").mockRejectedValue(
      new Error("network unreachable"),
    )

    const user = userEvent.setup()
    render(<ModelDownloadBanner />, { wrapper })

    const button = await screen.findByRole("button", { name: /download now/i })
    await user.click(button)

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Model download failed",
        expect.objectContaining({
          description: expect.stringContaining("network unreachable"),
        }),
      ),
    )
    // Banner still visible so user can retry
    expect(screen.getByRole("alert")).toBeInTheDocument()
  })

  it("respects an existing dismissed flag and never queries", async () => {
    window.localStorage.setItem(
      "cerid-model-download-banner-dismissed", "true",
    )
    const fetchSpy = vi.spyOn(settingsApi, "fetchModelsStatus")
    const { container } = render(<ModelDownloadBanner />, { wrapper })

    // No fetch should fire (enabled:false on the query)
    await new Promise((r) => setTimeout(r, 50))
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(container.querySelector("[role=alert]")).toBeNull()
  })
})
