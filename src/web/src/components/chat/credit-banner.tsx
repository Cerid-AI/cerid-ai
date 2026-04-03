// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState, useCallback } from "react"
import { AlertTriangle, ExternalLink, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { fetchHealth } from "@/lib/api"

const DISMISS_KEY = "cerid:credit-banner-dismissed"
const POLL_INTERVAL_MS = 30_000

/**
 * Slim, dismissible banner that appears at the top of the chat area when
 * OpenRouter credits are exhausted. Polls /health every 30s and auto-hides
 * once the flag clears (i.e. a successful Bifrost call happened).
 */
export function CreditBanner() {
  const [exhausted, setExhausted] = useState(false)
  const [dismissed, setDismissed] = useState(() => {
    try {
      return sessionStorage.getItem(DISMISS_KEY) === "1"
    } catch {
      return false
    }
  })

  const poll = useCallback(async () => {
    try {
      const health = await fetchHealth()
      const isExhausted = health.openrouter_credits_exhausted === true
      setExhausted(isExhausted)
      // Auto-show again if credits re-exhaust after a previous dismiss
      if (!isExhausted) {
        setDismissed(false)
        try {
          sessionStorage.removeItem(DISMISS_KEY)
        } catch { /* noop */ }
      }
    } catch {
      // Health endpoint unreachable — don't show banner
    }
  }, [])

  useEffect(() => {
    void (async () => { await poll() })()
    const id = setInterval(() => void poll(), POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [poll])

  if (!exhausted || dismissed) return null

  const handleDismiss = () => {
    setDismissed(true)
    try {
      sessionStorage.setItem(DISMISS_KEY, "1")
    } catch { /* noop */ }
  }

  return (
    <div className="flex items-center gap-2 border-b bg-amber-500/10 px-4 py-1.5 text-xs">
      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
      <span className="flex-1 text-amber-700 dark:text-amber-400">
        OpenRouter credits exhausted — verification and smart features are degraded
      </span>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 gap-1 text-xs text-amber-700 hover:text-amber-900 dark:text-amber-400 dark:hover:text-amber-300"
        onClick={() => window.open("https://openrouter.ai/settings/credits", "_blank")}
      >
        Add Credits
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
