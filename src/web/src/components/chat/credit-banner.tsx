// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState, useCallback } from "react"
import { AlertTriangle, ExternalLink, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { fetchHealthStatus } from "@/lib/api"
import { logSwallowedError } from "@/lib/log-swallowed"

// Separate dismiss keys so dismissing one degraded signal doesn't silence
// an unrelated one that appears later (e.g. user dismisses "credits" then
// auth fails — the auth banner must still show).
const DISMISS_KEY_CREDITS = "cerid:credit-banner-dismissed"
const DISMISS_KEY_AUTH = "cerid:auth-banner-dismissed"
const POLL_INTERVAL_MS = 30_000

type BannerKind = "auth-rejected" | "credits-exhausted"

interface BannerCopy {
  dismissKey: string
  message: string
  ctaLabel: string
  ctaUrl: string
}

const COPY: Record<BannerKind, BannerCopy> = {
  "auth-rejected": {
    dismissKey: DISMISS_KEY_AUTH,
    message: "Invalid OpenRouter API key — chat and verification will fail until you set a valid key",
    ctaLabel: "Update Key",
    ctaUrl: "https://openrouter.ai/settings/keys",
  },
  "credits-exhausted": {
    dismissKey: DISMISS_KEY_CREDITS,
    message: "OpenRouter credits exhausted — verification and smart features are degraded",
    ctaLabel: "Add Credits",
    ctaUrl: "https://openrouter.ai/settings/credits",
  },
}

function readDismissed(key: string): boolean {
  try {
    return sessionStorage.getItem(key) === "1"
  } catch {
    return false
  }
}

/**
 * Slim, dismissible banner surfaced at the top of the chat area when the
 * upstream provider is in a degraded state. Polls /health/status every 30 s
 * and renders the highest-priority active signal:
 *
 *   1. ``openrouter_auth_ok === false`` — key rejected (hard blocker)
 *   2. ``openrouter_credits_exhausted === true`` — quota out
 *
 * Each signal has its own dismiss key so acknowledging one does not hide
 * the other when it appears later. Both auto-unhide when their underlying
 * condition clears (e.g. user rotates a valid key, tops up credits).
 */
export function CreditBanner() {
  const [authRejected, setAuthRejected] = useState(false)
  const [creditsExhausted, setCreditsExhausted] = useState(false)
  const [dismissedCredits, setDismissedCredits] = useState(() => readDismissed(DISMISS_KEY_CREDITS))
  const [dismissedAuth, setDismissedAuth] = useState(() => readDismissed(DISMISS_KEY_AUTH))

  const poll = useCallback(async () => {
    try {
      const health = await fetchHealthStatus()
      // openrouter_auth_ok is tri-state: true = OK, false = rejected,
      // null/undefined = no key configured (setup flow surfaces that elsewhere).
      const isAuthRejected = health.openrouter_auth_ok === false
      const isCreditsExhausted = health.openrouter_credits_exhausted === true

      setAuthRejected(isAuthRejected)
      setCreditsExhausted(isCreditsExhausted)

      // Auto-show again when a previously-cleared signal recurs post-dismiss.
      if (!isAuthRejected) {
        setDismissedAuth(false)
        try { sessionStorage.removeItem(DISMISS_KEY_AUTH) } catch (err) { logSwallowedError(err, "sessionStorage.removeItem", { key: DISMISS_KEY_AUTH }) }
      }
      if (!isCreditsExhausted) {
        setDismissedCredits(false)
        try { sessionStorage.removeItem(DISMISS_KEY_CREDITS) } catch (err) { logSwallowedError(err, "sessionStorage.removeItem", { key: DISMISS_KEY_CREDITS }) }
      }
    } catch {
      // Health endpoint unreachable — don't render anything.
    }
  }, [])

  useEffect(() => {
    void (async () => { await poll() })()
    const id = setInterval(() => void poll(), POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [poll])

  // Pick the highest-priority active, un-dismissed signal.
  let kind: BannerKind | null = null
  if (authRejected && !dismissedAuth) kind = "auth-rejected"
  else if (creditsExhausted && !dismissedCredits) kind = "credits-exhausted"
  if (!kind) return null

  const copy = COPY[kind]

  const handleDismiss = () => {
    if (kind === "auth-rejected") setDismissedAuth(true)
    else setDismissedCredits(true)
    try { sessionStorage.setItem(copy.dismissKey, "1") } catch (err) { logSwallowedError(err, "sessionStorage.setItem", { key: copy.dismissKey }) }
  }

  return (
    <div className="flex items-center gap-2 border-b bg-amber-500/10 px-4 py-1.5 text-xs">
      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
      <span className="flex-1 text-amber-700 dark:text-amber-400">
        {copy.message}
      </span>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 gap-1 text-xs text-amber-700 hover:text-amber-900 dark:text-amber-400 dark:hover:text-amber-300"
        onClick={() => window.open(copy.ctaUrl, "_blank")}
      >
        {copy.ctaLabel}
        <ExternalLink className="h-3 w-3" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
        onClick={handleDismiss}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  )
}
