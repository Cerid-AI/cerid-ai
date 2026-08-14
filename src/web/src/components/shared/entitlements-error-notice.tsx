// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Shared "plan status unavailable" notice for every `useEntitlements()`
 * consumer (WB-09). A failed `GET /billing/capabilities` used to render
 * silently as the community tier everywhere: locked badges, "Requires Pro
 * plan" upsells, and disabled controls all looked identical to a real
 * entitlement decision, so a paying customer watched their Pro features
 * vanish with no error. Every call site that gates on `forFlag`/`forDef`/
 * `tier`/`licenseState` renders one of these when `isError` is true,
 * instead of proceeding on the fallback tier as if it were confirmed.
 */

import { AlertTriangle } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { cn } from "@/lib/utils"

/** Page/section-level banner. */
export function EntitlementsUnavailableBanner({ className }: { className?: string }) {
  return (
    <Alert variant="destructive" className={className} data-testid="entitlements-unavailable-banner">
      <AlertTriangle className="h-4 w-4" aria-hidden="true" />
      <AlertTitle>Plan status unavailable</AlertTitle>
      <AlertDescription>
        Couldn&apos;t confirm your plan with the license server. Pro features may
        show as locked until this resolves — your plan hasn&apos;t changed.
      </AlertDescription>
    </Alert>
  )
}

/** Compact inline form for a single row, card, or popover — anywhere a full
    Alert banner would overwhelm the layout. */
export function EntitlementsUnavailableNote({ className }: { className?: string }) {
  return (
    <p
      role="alert"
      data-testid="entitlements-unavailable-note"
      className={cn("flex items-center gap-1 text-label-xs text-destructive", className)}
    >
      <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden="true" />
      Couldn&apos;t confirm your plan — retry to check for Pro features.
    </p>
  )
}
