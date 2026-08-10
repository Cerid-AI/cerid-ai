// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Public/community edition: Plan & Billing.
//
// Checkout is hosted on cerid.ai — no payment provider is ever contacted from
// a self-hosted build. Everything else is local: the 14-day trial is granted
// by this server, and a purchased key is validated offline. Before v1.0.2 this
// pane was a static stub whose only link (cerid.ai/pro) 404'd, so a paying
// customer running the open-core build had no way to activate.

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, Crown, ExternalLink, Sparkles } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { SettingRow } from "@/components/settings/settings-primitives"
import { getDef } from "@/lib/settings-registry"
import {
  activateLicense,
  fetchLicenseStatus,
  startTrial,
  type LicenseStatus,
} from "@/lib/api/license"
import type { SettingsCategoryPageProps } from "./page-props"

const PRICING_URL = "https://cerid.ai/pricing"

function tierLabel(tier: string): string {
  return tier.charAt(0).toUpperCase() + tier.slice(1)
}

export default function PlanBillingCategory({ settings }: SettingsCategoryPageProps) {
  const qc = useQueryClient()
  const [key, setKey] = useState("")

  const { data, isLoading, isError } = useQuery<LicenseStatus>({
    queryKey: ["license-status"],
    queryFn: fetchLicenseStatus,
    staleTime: 60_000,
  })

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["license-status"] })
    void qc.invalidateQueries({ queryKey: ["settings"] })
  }

  const activate = useMutation({
    mutationFn: () => activateLicense(key.trim()),
    onSuccess: () => {
      setKey("")
      refresh()
    },
  })

  const trial = useMutation({ mutationFn: startTrial, onSuccess: refresh })

  const currentDef = getDef("plan.tier.current")!
  const trialDef = getDef("plan.trial.start")!
  const licenseDef = getDef("plan.license.key")!
  const manageDef = getDef("plan.tier.manage")!

  // Fall back to the tier the settings payload already carries, so a failed
  // license fetch degrades to the old read-only behaviour instead of a blank
  // pane. isError is read explicitly — `!data` alone would render the error
  // state as "Community", quietly misreporting a paid customer's plan.
  const tier = data?.tier ?? settings.feature_tier ?? "community"
  const isPaid = tier === "pro" || tier === "enterprise"

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader className="pb-2">
          <span className="text-label-xs uppercase tracking-wider text-muted-foreground">Plan</span>
        </CardHeader>
        <CardContent className="grid gap-3">
          <SettingRow def={currentDef}>
            {isLoading ? (
              <Skeleton className="h-6 w-24" />
            ) : (
              <div className="flex items-center gap-2">
                <Crown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                <Badge variant={isPaid ? "default" : "secondary"}>{tierLabel(tier)}</Badge>
                {data?.source === "trial" && data.trial.days_remaining != null && (
                  <span className="text-label-xs text-muted-foreground">
                    trial — {data.trial.days_remaining}{" "}
                    {data.trial.days_remaining === 1 ? "day" : "days"} left
                  </span>
                )}
                {data?.key_masked && (
                  <span className="text-label-xs text-muted-foreground">{data.key_masked}</span>
                )}
              </div>
            )}
          </SettingRow>

          {isError && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                Couldn&apos;t reach the licensing endpoint on this server. The plan shown may be
                out of date.
              </AlertDescription>
            </Alert>
          )}

          {/* Offered only while it can still be used — a permanently disabled
              "start trial" button is noise on an already-Pro install. */}
          {data?.trial.available && !isPaid && (
            <SettingRow def={trialDef}>
              <div className="flex flex-col items-end gap-1">
                <Button
                  size="sm"
                  onClick={() => trial.mutate()}
                  disabled={trial.isPending}
                >
                  <Sparkles className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                  {trial.isPending ? "Starting…" : "Start 14-day free trial"}
                </Button>
                <span className="text-label-xs text-muted-foreground">No credit card required</span>
                {trial.isError && (
                  <span className="text-label-xs text-destructive">
                    {(trial.error as Error).message}
                  </span>
                )}
              </div>
            </SettingRow>
          )}

          <SettingRow def={licenseDef}>
            <div className="flex flex-col items-end gap-1">
              <div className="flex items-center gap-2">
                <Input
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder="CERID-PRO-…"
                  className="w-64 font-mono text-xs"
                  aria-label="License key"
                  spellCheck={false}
                />
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => activate.mutate()}
                  disabled={!key.trim() || activate.isPending}
                >
                  {activate.isPending ? "Activating…" : "Activate"}
                </Button>
              </div>
              {activate.isError && (
                <span className="text-label-xs text-destructive">
                  {(activate.error as Error).message}
                </span>
              )}
              {activate.isSuccess && (
                <span className="text-label-xs text-emerald-500">License activated.</span>
              )}
            </div>
          </SettingRow>

          <SettingRow def={manageDef}>
            <Button asChild variant="outline" size="sm">
              <a href={PRICING_URL} target="_blank" rel="noreferrer">
                {isPaid ? "Manage at cerid.ai" : "See plans"}
                <ExternalLink className="ml-1.5 h-3.5 w-3.5" aria-hidden="true" />
              </a>
            </Button>
          </SettingRow>

          <p className="text-sm leading-relaxed text-muted-foreground">
            {isPaid
              ? `${tierLabel(tier)} is active — all ${tierLabel(tier).toLowerCase()} features are enabled on this server.`
              : "Pro unlocks the cloud and Apple connectors, Meeting Capture, custom Smart RAG, and advanced analytics. Upgrading takes one license key — no reinstall."}
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
