/**
 * App-wide TanStack Query client with cache-level error handlers.
 *
 * Error-handling contract (R3-1 scope):
 *   - MUTATIONS: every failure produces a toast (fire-and-forget writes
 *     need user-visible feedback; per-hook onError is no longer required).
 *   - QUERIES:  failures surface through `useQuery().isError` in the
 *     consuming component. The cache-level handler here only logs 5xx
 *     errors for future Sentry aggregation; it does NOT toast, because
 *     components typically render their own empty/error states and a
 *     toast on top would double-surface the failure. This is deliberate.
 *
 * Frontend Sentry wiring is deferred; the console.error structured-log
 * shape is Sentry-ready — see the "TODO(sentry)" comment at the call
 * site for the migration shape.
 */

// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

const MAX_TOAST_LEN = 240

/**
 * Safely coerce any thrown value to a user-readable message string.
 */
export function extractMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === "string") return err
  return "Something went wrong"
}

/**
 * Singleton QueryClient with cache-level error handlers so every
 * useMutation failure produces a visible toast without per-hook wiring.
 * Exported separately from main.tsx so tests can import it in isolation.
 */
export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (err, query) => {
      const status = (err as { status?: number })?.status
      // 5xx → log for later Sentry wiring; 4xx handled locally by useQuery consumers
      if (status && status >= 500) {
        console.error("query.failure", err, { queryKey: query.queryKey, status })
      }
    },
  }),
  mutationCache: new MutationCache({
    onError: (err, _vars, _ctx, mutation) => {
      const fullMessage = extractMessage(err)
      const displayMessage =
        fullMessage.length > MAX_TOAST_LEN
          ? fullMessage.slice(0, MAX_TOAST_LEN - 1) + "…"
          : fullMessage
      try {
        toast.error(displayMessage)
      } catch (toastErr) {
        console.error("mutation.failure.toast_threw", toastErr)
      }
      console.error("mutation.failure", {
        err,
        mutationKey: mutation.options.mutationKey ?? [],
        fullMessage,
      })
      // TODO(sentry): migrate to Sentry.captureException(err, { extra: { mutationKey, fullMessage } })
    },
  }),
  defaultOptions: {
    queries: {
      // 30s stale-time prevents the refetch storm that used to hit the
      // backend whenever a user flipped between Chat/KB/Health panes
      // in rapid succession. Individual hooks can override with their
      // own staleTime for faster-moving data (health polling = 10s,
      // credits = 60s).
      staleTime: 30_000,
      // Retry server errors, skip 4xx client errors
      retry: (failureCount, err) => {
        const status = (err as { status?: number })?.status
        if (status && status >= 400 && status < 500) return false
        return failureCount < 2
      },
      // refetchOnWindowFocus defaults to true in TanStack Query v5 which
      // triggers a cascade of refetches every time the user Cmd-Tabs
      // back to the tab. We already have refetchInterval set on the
      // hooks that actually need live updates, so turning focus refetch
      // off cuts redundant traffic without losing freshness.
      refetchOnWindowFocus: false,
      // Same reasoning: refetchOnMount defaults to true, which causes
      // a new GET on every pane switch even when the cached data is
      // under staleTime. "always" → "true" respects staleTime semantics.
      refetchOnMount: true,
    },
    mutations: {
      // No auto-retry on mutations — user should be in control.
      retry: false,
    },
  },
})
