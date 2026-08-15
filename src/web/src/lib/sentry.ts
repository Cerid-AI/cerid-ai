// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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
  // Runtime config (window.__ENV__ from docker-entrypoint.sh) takes precedence
  // over build-time Vite env vars — matches the pattern in lib/api/common.ts.
  // Lets operators rotate the DSN by restarting the container, no rebuild.
  const runtimeEnv = (globalThis as Record<string, unknown>).__ENV__ as
    | Record<string, string>
    | undefined
  const dsn = runtimeEnv?.VITE_SENTRY_DSN_WEB || import.meta.env.VITE_SENTRY_DSN_WEB
  if (!dsn) return null
  const release =
    runtimeEnv?.VITE_APP_VERSION || import.meta.env.VITE_APP_VERSION || __APP_VERSION__ || "dev"

  try {
    // Plain dynamic import so Vite resolves and code-splits it. It carried a
    // `/* @vite-ignore */` pragma and an `as string` cast, which together told
    // Vite to leave the specifier alone — so the built bundle asked the browser
    // to import a bare "@sentry/react", which no browser can resolve without an
    // import map. Sentry could therefore never initialize in a built app; the
    // catch below swallowed the failure and returned null, so it looked like
    // "no DSN configured" rather than a broken import. @sentry/react is a real
    // dependency (^10.69.0), so nothing needed to be hidden from the bundler.
    const Sentry = (await import("@sentry/react")) as unknown as SentryShape
    Sentry.init({
      dsn,
      release,
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
