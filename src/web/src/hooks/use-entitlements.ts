// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useEntitlements — the ONE client-side entitlement derivation (J-3 / J-6),
 * replacing the five independent `isPro` re-derivations. Wraps the existing
 * `GET /billing/capabilities` query (same query key as the Plan & Billing
 * capability matrix, so the cache is shared).
 *
 * Three distinct non-available states:
 *  - "locked"   — tier below the feature's required tier ("View plan" path).
 *  - "flag-off" — tier is sufficient but the server flag is disabled
 *                 (never show "upgrade" advice to a Pro user).
 *  - "degraded" — feature's dependency is down. The capabilities endpoint
 *                 doesn't report health, so this hook never derives it;
 *                 callers pass it through from their own health source and
 *                 render `DegradedFeatureNote` (dependency down ≠ locked).
 *
 * The client lock is a mirror; the server 403 stays authoritative.
 */

import { useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { fetchCapabilities, type FeatureTier } from "@/lib/api/billing"
import type { SettingDef } from "@/lib/settings-registry"

export type EntitlementState = "available" | "locked" | "flag-off" | "degraded"

export interface EntitlementInfo {
  state: EntitlementState
  /** Set when state === "locked". */
  requiredTier?: FeatureTier
}

const TIER_RANK: Record<FeatureTier, number> = { community: 0, pro: 1, enterprise: 2 }

const AVAILABLE: EntitlementInfo = { state: "available" }

export interface Entitlements {
  tier: FeatureTier
  isLoading: boolean
  isError: boolean
  /** Resolve a single server feature flag (optionally with the registry
      entitlement tier as fallback while capabilities load). */
  forFlag: (featureFlag?: string, entitlement?: "pro" | "enterprise") => EntitlementInfo
  /** Resolve a registry def's lock state. Defs without `entitlement` are
      always available. */
  forDef: (def: Pick<SettingDef, "entitlement" | "featureFlag">) => EntitlementInfo
}

export function useEntitlements(): Entitlements {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["billing-capabilities"],
    queryFn: fetchCapabilities,
    staleTime: 60_000,
  })

  const tier: FeatureTier = data?.tier ?? "community"

  const forFlag = useCallback(
    (featureFlag?: string, entitlement?: "pro" | "enterprise"): EntitlementInfo => {
      if (!featureFlag && !entitlement) return AVAILABLE
      const detail = featureFlag ? data?.features?.[featureFlag] : undefined
      if (detail) {
        if (detail.enabled) return AVAILABLE
        if (TIER_RANK[tier] < TIER_RANK[detail.tier_required]) {
          return { state: "locked", requiredTier: detail.tier_required }
        }
        return { state: "flag-off" }
      }
      // Capabilities not loaded (or flag unknown): fall back to the declared
      // registry tier so locked rows render locked instead of flashing live.
      if (entitlement && TIER_RANK[tier] < TIER_RANK[entitlement]) {
        return { state: "locked", requiredTier: entitlement }
      }
      return AVAILABLE
    },
    [data, tier],
  )

  const forDef = useCallback(
    (def: Pick<SettingDef, "entitlement" | "featureFlag">): EntitlementInfo => {
      if (!def.entitlement) return AVAILABLE
      return forFlag(def.featureFlag, def.entitlement)
    },
    [forFlag],
  )

  return { tier, isLoading, isError, forFlag, forDef }
}
