// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Workstream E Phase E.6.6 — first-query model-download notification.
//
// On lean Docker images (CERID_PRELOAD_MODELS=false, the default after
// Phase E.6.5), the reranker + embedder ONNX models download on first
// inference call (5–15s blocking stall). The Settings → Inference
// Models card lets users warm the cache proactively, but most users
// won't see it until something feels slow. This banner closes that gap:
//
//   - On app load, polls /setup/models/status
//   - If neither model cached → sticky banner explaining the one-time
//     download trigger + a "Download now" button (calls preloadModels)
//     + a "Dismiss" that hides the banner for the session (localStorage)
//   - If a download is in flight (server reports `loading: true`),
//     swaps the banner copy for a spinner: "Downloading inference
//     models — one-time setup..."
//   - When status flips to cached, fires a one-shot success toast
//     ("Inference models ready") and dismisses the banner
//
// Polling cadence is state-dependent: 30s when idle/uncached, 2s when
// loading (so the user sees the success transition quickly).

import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { CheckCircle2, Download, Info, Loader2, X } from "lucide-react"

import {
  fetchModelsStatus,
  preloadModels,
  type ModelsStatusResponse,
} from "@/lib/api/settings"
import { Button } from "@/components/ui/button"

const DISMISS_KEY = "cerid-model-download-banner-dismissed"
const POLL_IDLE_MS = 30_000
const POLL_LOADING_MS = 2_000

type BannerStatus = "hidden" | "idle_uncached" | "loading" | "cached"

function isBothCached(status: ModelsStatusResponse | undefined): boolean {
  if (!status) return false
  return status.reranker.cached === true && status.embedder.cached === true
}

function isAnyLoading(status: ModelsStatusResponse | undefined): boolean {
  if (!status) return false
  return Boolean(status.reranker.loading) || Boolean(status.embedder.loading)
}

function isAnyUncached(status: ModelsStatusResponse | undefined): boolean {
  if (!status) return false
  return status.reranker.cached === false || status.embedder.cached === false
}

function readDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISS_KEY) === "true"
  } catch {
    return false
  }
}

export function ModelDownloadBanner() {
  const queryClient = useQueryClient()
  const [dismissed, setDismissed] = useState<boolean>(() => readDismissed())
  const [downloading, setDownloading] = useState(false)
  // Track whether we've ever observed a not-cached state. The success
  // toast should only fire on the cached transition (not on initial mount
  // when models were already cached).
  const sawUncachedRef = useRef<boolean>(false)
  const announcedReadyRef = useRef<boolean>(false)

  const { data: status, refetch } = useQuery<ModelsStatusResponse>({
    queryKey: ["model-download-banner-status"],
    queryFn: fetchModelsStatus,
    refetchInterval: (query) => {
      const data = query.state.data as ModelsStatusResponse | undefined
      // Stop polling once both are cached AND we've already announced —
      // no reason to keep pinging /setup/models/status forever.
      if (isBothCached(data) && announcedReadyRef.current) return false
      // Fast cadence while a download is in flight so the success
      // transition lands within seconds.
      if (isAnyLoading(data)) return POLL_LOADING_MS
      // Slow background poll otherwise.
      return POLL_IDLE_MS
    },
    // Don't run while dismissed — saves an HTTP call per mount.
    enabled: !dismissed,
    // Share the cached status with the InferenceModelsCard's query so
    // the badges there reflect "now cached" without a separate fetch.
    staleTime: 5_000,
  })

  // Track that we've seen an uncached state during this session so the
  // success toast only fires on real cached transitions.
  useEffect(() => {
    if (isAnyUncached(status)) {
      sawUncachedRef.current = true
    }
  }, [status])

  // Fire the one-shot success toast when both models become cached AFTER
  // we've observed at least one uncached state. Also invalidate the
  // InferenceModelsCard's query so its badges update.
  useEffect(() => {
    if (
      sawUncachedRef.current &&
      !announcedReadyRef.current &&
      isBothCached(status)
    ) {
      announcedReadyRef.current = true
      toast.success("Inference models ready", {
        description: "Cached locally — semantic queries are now full speed.",
        duration: 5_000,
      })
      void queryClient.invalidateQueries({
        queryKey: ["inference-models-status"],
      })
    }
  }, [status, queryClient])

  // Reset the announcement flag if the user clears the cache somehow
  // (cached → uncached transition). Edge case but harmless.
  useEffect(() => {
    if (isAnyUncached(status)) {
      announcedReadyRef.current = false
    }
  }, [status])

  const handleDownload = async () => {
    setDownloading(true)
    try {
      await preloadModels()
      // refetch immediately so the banner flips to cached state
      await refetch()
    } catch (err) {
      // Surface the failure as a toast — the InferenceModelsCard has
      // richer error UX. Keep the banner visible so the user sees they
      // can retry.
      toast.error("Model download failed", {
        description: err instanceof Error ? err.message : String(err),
        duration: 8_000,
      })
    } finally {
      setDownloading(false)
    }
  }

  const handleDismiss = () => {
    try {
      window.localStorage.setItem(DISMISS_KEY, "true")
    } catch {
      // Storage disabled — banner just hides for this session
    }
    setDismissed(true)
  }

  // ── Render decision ─────────────────────────────────────────────────
  const bannerStatus: BannerStatus = (() => {
    if (dismissed) return "hidden"
    if (!status) return "hidden"
    if (isBothCached(status)) return "cached"
    if (isAnyLoading(status) || downloading) return "loading"
    return "idle_uncached"
  })()

  if (bannerStatus === "hidden" || bannerStatus === "cached") {
    return null
  }

  if (bannerStatus === "loading") {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-3 border-b border-blue-500/30 bg-blue-500/5 px-4 py-2 text-sm"
      >
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-blue-500" />
        <div className="flex-1">
          <span className="font-medium">
            Downloading inference models
          </span>
          <span className="ml-2 text-muted-foreground">
            One-time setup (~38 MB) — this takes 5–15 seconds, then
            subsequent queries are full speed.
          </span>
        </div>
      </div>
    )
  }

  // idle_uncached: show the proactive warning + Download Now action
  return (
    <div
      role="alert"
      className="flex items-center gap-3 border-b border-amber-500/30 bg-amber-500/5 px-4 py-2 text-sm"
    >
      <Info className="h-4 w-4 shrink-0 text-amber-500" />
      <div className="flex-1">
        <span className="font-medium">First semantic query will trigger model download</span>
        <span className="ml-2 text-muted-foreground">
          One-time setup (~38 MB, ~15s). Download now or accept the
          first-query stall.
        </span>
      </div>
      <Button
        size="sm"
        variant="default"
        onClick={handleDownload}
        disabled={downloading}
      >
        {downloading ? (
          <>
            <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            Downloading…
          </>
        ) : (
          <>
            <Download className="mr-1.5 h-3 w-3" />
            Download now
          </>
        )}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={handleDismiss}
        aria-label="Dismiss model download notification"
        title="Dismiss"
      >
        <X className="h-3 w-3" />
      </Button>
    </div>
  )
}

// Test-only — clear the dismissed flag so a unit test can re-render the
// banner without DOM-state leakage between cases.
export function _resetDismissedForTest(): void {
  try {
    window.localStorage.removeItem(DISMISS_KEY)
  } catch {
    // ignore
  }
}

// Re-export for the unit-test file's "just-cached transition" assertion
export { CheckCircle2 }
