// src/web/src/lib/log-swallowed.ts
// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { addBreadcrumb } from "./sentry"

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
 *   - In production: addBreadcrumb into Sentry at info level. The
 *     breadcrumb is keyed by `reason` so swallows can be filtered;
 *     they don't surface as Sentry issues unless escalated.
 */
export function logSwallowedError(
  err: unknown,
  reason: string,
  extra?: Record<string, unknown>,
): void {
  const errMessage = err instanceof Error ? err.message : String(err)
  if (import.meta.env.DEV) {
    console.warn(`[swallowed] ${reason}`, err, extra ?? {})
  }
  // Always add a breadcrumb (no-op when Sentry isn't initialized).
  addBreadcrumb(
    "swallowed",
    `${reason}: ${errMessage}`,
    { ...(extra ?? {}), error_name: err instanceof Error ? err.name : "non-error" },
    "info",
  )
}
