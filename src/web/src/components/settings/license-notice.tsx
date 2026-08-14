// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * License notices for the two states that warrant one.
 *
 * `unlicensed_pro` — paid features enabled by CERID_TIER with no key and no
 * trial. Not dismissible: the point is that this install is never mistaken for
 * a licensed one. Nothing is blocked or degraded; the features keep working.
 *
 * `trial_expired` — the trial was used and lapsed. Dismissible, and it stays
 * dismissed for a week, because this user already saw what Pro does.
 *
 * Every other state renders nothing. A Core user who has never trialed is the
 * top of the funnel, not a debtor — they are never nagged here.
 */

import { useState } from "react"
import { AlertTriangle, Clock, X } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useNavigation } from "@/contexts/navigation-context"

const PRICING_URL = "https://cerid.ai/pricing"
const SNOOZE_KEY = "cerid.trialExpiredSnoozedUntil"
const SNOOZE_DAYS = 7

function snoozedUntil(): number {
  const raw = localStorage.getItem(SNOOZE_KEY)
  const parsed = raw ? Number(raw) : 0
  // A corrupt value must not snooze forever.
  return Number.isFinite(parsed) ? parsed : 0
}

export function LicenseNotice({ className }: { className?: string }) {
  const { licenseState, isError: entitlementsError } = useEntitlements()
  const { goTo } = useNavigation()
  const [snoozed, setSnoozed] = useState(() => Date.now() < snoozedUntil())

  // A failed capabilities fetch must not read as "nothing to report" — that
  // silently hid this exact banner (and the unlicensed-Pro state it warns
  // about) from the operator it exists for.
  if (entitlementsError) {
    return (
      <Alert variant="destructive" className={className} data-testid="license-status-unavailable">
        <AlertTriangle className="h-4 w-4" aria-hidden="true" />
        <AlertDescription className="text-xs">
          Couldn&apos;t reach the license server to confirm this install&apos;s plan status.
        </AlertDescription>
      </Alert>
    )
  }

  if (licenseState === "unlicensed_pro") {
    return (
      <Alert
        variant="destructive"
        className={cn("border-amber-500/40 bg-amber-500/10", className)}
        data-testid="unlicensed-pro-notice"
      >
        <AlertTriangle className="h-4 w-4" aria-hidden="true" />
        <AlertDescription className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span>
            {/* Two causes reach this state — a CERID_TIER pin with no license, and a
                license that cannot be verified because signature checking is off — so
                the wording covers both rather than asserting one. */}
            <strong>Unlicensed copy of Cerid Pro.</strong> Paid features are running on
            this server without a verified license.
          </span>
          <span className="flex gap-2">
            <a
              href={PRICING_URL}
              target="_blank"
              rel="noreferrer"
              className="font-medium underline underline-offset-2 hover:no-underline"
            >
              Get a license
            </a>
            <button
              type="button"
              onClick={() => goTo("settings", { category: "plan" })}
              className="font-medium underline underline-offset-2 hover:no-underline"
            >
              Start the free trial
            </button>
          </span>
        </AlertDescription>
      </Alert>
    )
  }

  if (licenseState === "trial_expired" && !snoozed) {
    return (
      <Alert className={cn("border-border", className)} data-testid="trial-expired-notice">
        <Clock className="h-4 w-4" aria-hidden="true" />
        <AlertDescription className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          <span>Your Cerid Pro trial has ended — Pro features are off.</span>
          <a
            href={PRICING_URL}
            target="_blank"
            rel="noreferrer"
            className="font-medium underline underline-offset-2 hover:no-underline"
          >
            See plans
          </a>
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto h-5 w-5"
            aria-label="Dismiss for a week"
            onClick={() => {
              localStorage.setItem(
                SNOOZE_KEY,
                String(Date.now() + SNOOZE_DAYS * 86400_000),
              )
              setSnoozed(true)
            }}
          >
            <X className="h-3 w-3" aria-hidden="true" />
          </Button>
        </AlertDescription>
      </Alert>
    )
  }

  return null
}

/** Compact status-bar form: presence alone is the message. */
export function LicenseStatusBadge() {
  const { licenseState, isError: entitlementsError } = useEntitlements()
  const { goTo } = useNavigation()

  if (entitlementsError) {
    return (
      <span
        role="alert"
        title="Couldn't reach the license server to confirm plan status"
        className="flex items-center gap-1 rounded bg-destructive/20 px-1.5 py-0.5 text-destructive"
      >
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        Plan unknown
      </span>
    )
  }

  if (licenseState !== "unlicensed_pro") return null

  return (
    <button
      type="button"
      onClick={() => goTo("settings", { category: "plan" })}
      title="Paid features are enabled without a license — click to license this copy"
      className="flex items-center gap-1 rounded bg-amber-500/20 px-1.5 py-0.5 text-amber-500"
    >
      <AlertTriangle className="h-3 w-3" aria-hidden="true" />
      Unlicensed
    </button>
  )
}
