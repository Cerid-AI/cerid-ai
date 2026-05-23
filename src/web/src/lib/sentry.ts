// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Frontend Sentry wrapper. No-ops in dev / test / missing-DSN.
 * Init at bootstrap (main.tsx); call sites in log-swallowed.ts +
 * query-client.ts. Gated on `VITE_SENTRY_DSN_WEB`.
 */

// @sentry/react is loaded dynamically — typing it here would force
// every build env to have the package installed even when DSN is unset.
interface SentryShape {
  init: (options: Record<string, unknown>) => void
  captureException: (err: unknown, hint?: { extra?: Record<string, unknown> }) => void
  addBreadcrumb: (crumb: {
    category: string
    message: string
    data?: Record<string, unknown>
    level?: string
  }) => void
}

let _sentry: SentryShape | null = null
let _initialized = false

/** Idempotent init. Returns the module on success, null in dev / no-DSN / load-fail. */
export async function initSentry(): Promise<SentryShape | null> {
  if (_initialized) return _sentry
  _initialized = true

  if (import.meta.env.DEV) return null
  const dsn = import.meta.env.VITE_SENTRY_DSN_WEB
  if (!dsn) return null

  try {
    const Sentry = (await import(/* @vite-ignore */ "@sentry/react" as string)) as SentryShape
    Sentry.init({
      dsn,
      release: import.meta.env.VITE_APP_VERSION ?? "dev",
      environment: import.meta.env.MODE,
      tracesSampleRate: 0.1,
      replaysSessionSampleRate: 0,
      replaysOnErrorSampleRate: 1.0,
      beforeSend(event: { contexts?: { react?: { componentStack?: unknown } } }) {
        // Truncate component stacks — they may contain user-typed text.
        const stack = event.contexts?.react?.componentStack
        if (typeof stack === "string" && stack.length > 10_000) {
          event.contexts!.react!.componentStack = stack.slice(0, 10_000) + "...[truncated]"
        }
        return event
      },
    })
    _sentry = Sentry
    return Sentry
  } catch {
    return null
  }
}

/** Capture an exception. No-op when Sentry isn't initialized. */
export function captureException(err: unknown, extra?: Record<string, unknown>): void {
  if (!_sentry) return
  _sentry.captureException(err, { extra })
}

/** Add a breadcrumb (used for intentional swallows). No-op when uninit. */
export function addBreadcrumb(
  category: string,
  message: string,
  data?: Record<string, unknown>,
  level: "info" | "warning" | "error" = "info",
): void {
  if (!_sentry) return
  _sentry.addBreadcrumb({ category, message, data, level })
}
