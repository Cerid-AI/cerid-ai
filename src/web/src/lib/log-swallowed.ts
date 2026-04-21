// src/web/src/lib/log-swallowed.ts
// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Explicitly log a swallowed error with a named reason.
 *
 * Use in place of `catch { /* noop *\/ }` or `catch { /* ignore *\/ }`
 * blocks to make the swallow INTENTIONAL, named, and inspectable.
 * The `reason` tag is required — if you can't name why you're
 * swallowing, you probably shouldn't be.
 *
 * Contract:
 *   - In dev mode (import.meta.env.DEV): console.warn with the reason
 *     and the error, so engineers see the failure while working.
 *   - In production: no-op console output, but the structured-log shape
 *     is Sentry-ready so the future frontend-Sentry migration is a
 *     one-line change (see TODO below).
 *
 * TODO(sentry): when frontend Sentry lands, replace the dev-mode
 * console.warn with `Sentry.captureException(err, {level: "info",
 * tags: {swallowed_reason: reason}, extra})`. Both dev and prod paths
 * become a single captureException call.
 */
export function logSwallowedError(
  err: unknown,
  reason: string,
  extra?: Record<string, unknown>,
): void {
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.warn(`[swallowed] ${reason}`, err, extra ?? {})
  }
}
