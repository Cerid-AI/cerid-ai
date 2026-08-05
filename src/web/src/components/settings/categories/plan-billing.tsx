// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Public/community edition: Plan & Billing ships as a static stub (the
// SEXTANT successor to the pro-section stub). Licensing, upgrades, and
// checkout live on cerid.ai; this self-hosted build never talks to a
// payment provider.

import { Crown, ExternalLink } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { SettingRow } from "@/components/settings/settings-primitives"
import { getDef } from "@/lib/settings-registry"
import type { SettingsCategoryPageProps } from "./page-props"

export default function PlanBillingCategory({ settings }: SettingsCategoryPageProps) {
  const tier = settings.feature_tier || "community"
  const isPaid = tier === "pro" || tier === "enterprise"
  const tierLabel = tier.charAt(0).toUpperCase() + tier.slice(1)
  const currentDef = getDef("plan.tier.current")!
  const manageDef = getDef("plan.tier.manage")!

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader className="pb-2">
          <span className="text-label-xs uppercase text-muted-foreground tracking-wider">Plan</span>
        </CardHeader>
        <CardContent className="grid gap-3">
          <SettingRow def={currentDef}>
            <div className="flex items-center gap-2">
              <Crown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              <Badge variant={isPaid ? "default" : "secondary"}>{tierLabel}</Badge>
            </div>
          </SettingRow>
          <SettingRow def={manageDef}>
            <Button asChild variant="outline" size="sm">
              <a href="https://cerid.ai/pro" target="_blank" rel="noreferrer">
                Manage at cerid.ai
                <ExternalLink className="ml-1.5 h-3.5 w-3.5" aria-hidden="true" />
              </a>
            </Button>
          </SettingRow>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {isPaid
              ? `${tierLabel} tier is active — all ${tierLabel.toLowerCase()} features are enabled on this server.`
              : "Upgrading later takes one license key — no reinstall needed."}
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
