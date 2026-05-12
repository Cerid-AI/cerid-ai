// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useChatVirtualization — feature flag for the v0.93.5 chat message
 * virtualization (Cycle 3.2 follow-on).
 *
 * Resolution order:
 *
 *   1. ``localStorage.cerid:chat-virtualized`` ("true" / "false") — wins
 *      when explicitly set by the user via the adaptive recommender
 *      banner's "Enable now" action.
 *   2. ``VITE_CHAT_VIRTUALIZATION`` env var ("true" enables) — sets the
 *      default for a build.
 *   3. Otherwise OFF.
 *
 * The hook is read-only.  Flipping the flag from "true" → "false" or
 * vice versa requires a page reload by design — virtualization is a
 * load-bearing render-tree change and toggling it live would break the
 * scroll anchor state mid-conversation.
 *
 * See ``docs/plans/2026-05-12-chat-virtualization-sprint-plan.md`` for
 * the full design rationale.
 */
import { useEffect, useState } from "react"
import { logSwallowedError } from "@/lib/log-swallowed"

const ENV_DEFAULT = (() => {
  try {
    const v = (import.meta as { env?: Record<string, string> }).env?.VITE_CHAT_VIRTUALIZATION
    return v === "true" || v === "1"
  } catch {
    return false
  }
})()

const LOCALSTORAGE_KEY = "cerid:chat-virtualized"

function readFlag(): boolean {
  try {
    const v = localStorage.getItem(LOCALSTORAGE_KEY)
    if (v === "true") return true
    if (v === "false") return false
  } catch (err) {
    logSwallowedError(err, "use-chat-virtualization.readFlag")
  }
  return ENV_DEFAULT
}

export function useChatVirtualization(): boolean {
  const [enabled, setEnabled] = useState(readFlag)
  // Re-read on storage event so the recommender's "Enable now" PATCH
  // (which goes through the settings router, but also mirrors to
  // localStorage via the banner click handler) takes effect on the
  // next render without a hard reload.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === LOCALSTORAGE_KEY) {
        setEnabled(readFlag())
      }
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [])
  return enabled
}
