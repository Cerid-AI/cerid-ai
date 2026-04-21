// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from "react"
import { MCP_BASE } from "@/lib/api"

export interface ActivityEntry {
  agent: string
  message: string
  level: string
  timestamp: number
}

export type AgentActivityStatus =
  | "idle"
  | "connecting"
  | "open"
  | "retrying"
  | "unavailable"

export interface UseAgentActivityStreamOptions {
  /** Max event buffer; defaults to 100 (matches the prior inline behaviour). */
  maxEntries?: number
  /** If false, the stream is torn down until flipped back to true. */
  enabled?: boolean
  /** Override the endpoint URL (tests). */
  url?: string
  /** Override the maximum retry attempts before giving up. Default 10. */
  maxRetries?: number
}

export interface UseAgentActivityStreamResult {
  entries: ActivityEntry[]
  status: AgentActivityStatus
  /** Only set when status === "retrying" or "unavailable". */
  error: string | null
  retryCount: number
  /** Force a fresh reconnect, resetting the retry counter. */
  reset: () => void
}

const DEFAULT_MAX_RETRIES = 10
const BASE_BACKOFF_MS = 500
const MAX_BACKOFF_MS = 30_000

function computeBackoff(retryCount: number): number {
  // Exponential with a hard cap; retryCount starts at 0 for the first retry.
  return Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** retryCount)
}

/**
 * Subscribe to the MCP server's agent activity SSE stream.
 *
 * Hardening over the previous inline implementation:
 * - Exponential back-off (500ms → 30s cap), reset on successful message.
 * - `maxRetries` (default 10); after exhaustion the stream is abandoned and
 *   the caller can surface an "unavailable — reload to retry" state.
 * - Closes the `EventSource` on unmount (no leak when the user switches tabs).
 * - `enabled=false` tears the stream down so a parent can pause polling when
 *   the Agents tab is not visible.
 * - Listens to `document.visibilitychange` and pauses while the page is hidden.
 */
export function useAgentActivityStream(
  options: UseAgentActivityStreamOptions = {},
): UseAgentActivityStreamResult {
  const {
    maxEntries = 100,
    enabled = true,
    url,
    maxRetries = DEFAULT_MAX_RETRIES,
  } = options

  const [entries, setEntries] = useState<ActivityEntry[]>([])
  const [status, setStatus] = useState<AgentActivityStatus>("idle")
  const [error, setError] = useState<string | null>(null)
  const [retryCount, setRetryCount] = useState(0)
  // Force-reconnect bump: changing this triggers the effect to rebuild.
  const [resetToken, setResetToken] = useState(0)

  // Refs for values the effect needs without retriggering.
  const esRef = useRef<EventSource | null>(null)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryCountRef = useRef(0)
  const abandonedRef = useRef(false)

  const reset = useCallback(() => {
    abandonedRef.current = false
    retryCountRef.current = 0
    setRetryCount(0)
    setError(null)
    setResetToken((n) => n + 1)
  }, [])

  useEffect(() => {
    if (!enabled) {
      setStatus("idle")
      return () => {
        /* teardown handled below via the common cleanup path on re-run */
      }
    }

    const endpoint = url ?? `${MCP_BASE}/agents/activity/stream`
    let cancelled = false

    const clearRetryTimer = () => {
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current)
        retryTimerRef.current = null
      }
    }

    const teardown = () => {
      cancelled = true
      clearRetryTimer()
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
    }

    const connect = () => {
      if (cancelled || abandonedRef.current) return
      setStatus((prev) => (prev === "retrying" ? "retrying" : "connecting"))
      const es = new EventSource(endpoint)
      esRef.current = es

      es.onopen = () => {
        if (cancelled) return
        setStatus("open")
        setError(null)
      }

      es.onmessage = (event) => {
        if (cancelled) return
        // Successful payload resets the back-off counter so transient blips
        // don't compound into a 30s wait after a long-running connection.
        if (retryCountRef.current !== 0) {
          retryCountRef.current = 0
          setRetryCount(0)
        }
        try {
          const data = JSON.parse(event.data) as ActivityEntry
          setEntries((prev) => {
            const next = [...prev, data]
            return next.length > maxEntries ? next.slice(-maxEntries) : next
          })
        } catch {
          // Ignore heartbeats and malformed frames.
        }
      }

      es.onerror = () => {
        if (cancelled) return
        es.close()
        esRef.current = null

        const attempt = retryCountRef.current
        if (attempt >= maxRetries) {
          abandonedRef.current = true
          setStatus("unavailable")
          setError(
            "Agent activity unavailable — reload to retry.",
          )
          return
        }

        const delay = computeBackoff(attempt)
        retryCountRef.current = attempt + 1
        setRetryCount(attempt + 1)
        setStatus("retrying")
        setError(
          `Connection lost. Reconnecting in ${(delay / 1000).toFixed(1)}s` +
            ` (attempt ${attempt + 1}/${maxRetries})`,
        )

        retryTimerRef.current = setTimeout(() => {
          retryTimerRef.current = null
          connect()
        }, delay)
      }
    }

    const handleVisibility = () => {
      if (typeof document === "undefined") return
      if (document.visibilityState === "hidden") {
        // Pause while hidden — release the EventSource and any pending retry
        // so we aren't holding a browser connection slot in a background tab.
        if (esRef.current) {
          esRef.current.close()
          esRef.current = null
          setStatus("idle")
        }
        clearRetryTimer()
      } else if (!cancelled && !abandonedRef.current && !esRef.current) {
        // Resume immediately on return without waiting out a back-off delay;
        // reset the attempt counter since a hidden tab isn't a failure.
        retryCountRef.current = 0
        setRetryCount(0)
        connect()
      }
    }

    connect()
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibility)
    }

    return () => {
      teardown()
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibility)
      }
    }
  }, [enabled, url, maxEntries, maxRetries, resetToken])

  return { entries, status, error, retryCount, reset }
}

// Exposed for tests.
export const __testing__ = { computeBackoff, MAX_BACKOFF_MS, BASE_BACKOFF_MS }
