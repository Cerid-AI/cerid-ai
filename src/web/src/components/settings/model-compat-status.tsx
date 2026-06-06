// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ModelCompatStatus — surfaces GET /models/doctor: are the configured models
 * the most capable ones that actually run on this hardware, and are they
 * current?
 *
 * Hardware-aware (multi-profile): the backend keys everything off
 * CERID_HARDWARE_PROFILE, so this same component serves every backend. Shown in
 * Settings → Models and (compact) in the setup wizard's backend step.
 *
 * Findings map to the canonical status-colour vocabulary:
 *   error (incompatible)  → red    — model can't run on this hardware
 *   warn  (dead pin)      → amber  — pinned model gone from the catalog
 *   info  (local currency)→ muted  — a known-good / validated upgrade exists
 */

import { useQuery } from "@tanstack/react-query"
import { AlertTriangle, CheckCircle2, Cpu, Info, RefreshCw } from "lucide-react"

import { fetchModelDoctor, type ModelDoctorFinding, type ModelDoctorReport } from "@/lib/api"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

const SEVERITY_STYLE: Record<
  ModelDoctorFinding["severity"],
  { wrap: string; icon: typeof AlertTriangle; iconClass: string }
> = {
  error: { wrap: "bg-red-500/10 text-red-700 dark:text-red-300", icon: AlertTriangle, iconClass: "text-red-500" },
  warn: { wrap: "bg-amber-500/10 text-amber-700 dark:text-amber-300", icon: AlertTriangle, iconClass: "text-amber-500" },
  info: { wrap: "bg-muted text-muted-foreground", icon: Info, iconClass: "text-muted-foreground" },
}

function FindingRow({ f }: { f: ModelDoctorFinding }) {
  const s = SEVERITY_STYLE[f.severity]
  const Icon = s.icon
  return (
    <li className={`flex items-start gap-2 rounded-md px-2 py-1.5 ${s.wrap}`}>
      <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${s.iconClass}`} aria-hidden="true" />
      <span className="min-w-0 text-label-xs">
        <span className="font-medium">{f.role}</span>{" "}
        <span className="tabular-nums opacity-80">{f.model}</span>
        <span className="block opacity-90">{f.detail}</span>
      </span>
    </li>
  )
}

function ProfileBadge({ profile }: { profile: string }) {
  return (
    <Badge variant="outline" className="gap-1 text-label-xxs">
      <Cpu className="h-3 w-3" aria-hidden="true" />
      {profile === "unknown" ? "hardware unset" : profile}
    </Badge>
  )
}

export interface ModelCompatStatusProps {
  /** Compact mode for the setup wizard: profile + headline + known-good local set. */
  compact?: boolean
}

export function ModelCompatStatus({ compact = false }: ModelCompatStatusProps) {
  const { data, isLoading, isError, refetch, isFetching } = useQuery<ModelDoctorReport>({
    queryKey: ["model-doctor"],
    queryFn: fetchModelDoctor,
    staleTime: 60_000,
  })

  // 1. Loading
  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="model-compat-loading">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-8 w-full" />
      </div>
    )
  }

  // 2. Error
  if (isError || !data) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" aria-hidden="true" />
        <AlertDescription className="flex items-center justify-between gap-2">
          <span>Couldn&apos;t check model compatibility.</span>
          <Button variant="outline" size="sm" className="h-6" onClick={() => refetch()}>
            <RefreshCw className="mr-1 h-3 w-3" aria-hidden="true" />
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    )
  }

  const errors = (data.findings ?? []).filter((f) => f.severity === "error")
  const others = (data.findings ?? []).filter((f) => f.severity !== "error")

  // Compact (wizard): profile + headline + the known-good local set.
  if (compact) {
    const kg = data.known_good_local
    return (
      <div className="space-y-1.5" data-testid="model-compat-compact">
        <div className="flex items-center gap-2">
          <ProfileBadge profile={data.hardware_profile} />
          {errors.length > 0 ? (
            <span className="inline-flex items-center gap-1 text-label-xs text-red-600 dark:text-red-400">
              <AlertTriangle className="h-3 w-3" aria-hidden="true" />
              {errors.length} incompatible model{errors.length > 1 ? "s" : ""}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-label-xs text-green-600 dark:text-green-400">
              <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
              compatible
            </span>
          )}
        </div>
        {Object.keys(kg).length > 0 && (
          <p className="text-label-xs text-muted-foreground">
            Recommended local models:{" "}
            {Object.entries(kg)
              .map(([role, model]) => `${role} ${model}`)
              .join(" · ")}
          </p>
        )}
        <ul className="space-y-1">
          {errors.map((f) => (
            <FindingRow key={`${f.role}-${f.model}`} f={f} />
          ))}
        </ul>
      </div>
    )
  }

  // Full (settings) — Card with the 4th "success" state when all-clear.
  return (
    <Card>
      <CardHeader className="px-4 pb-2 pt-3">
        <CardDescription className="flex items-center justify-between gap-2 text-xs">
          <span className="flex items-center gap-1.5">
            Model compatibility
            <ProfileBadge profile={data.hardware_profile} />
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-1.5 text-muted-foreground"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Re-check model compatibility"
          >
            <RefreshCw className={`h-3 w-3 ${isFetching ? "animate-spin" : ""}`} aria-hidden="true" />
          </Button>
        </CardDescription>
      </CardHeader>
      <CardContent className="px-4 pb-3">
        {data.findings.length === 0 ? (
          <div className="flex items-center gap-2 rounded-md bg-green-500/10 px-2 py-1.5 text-label-xs text-green-700 dark:text-green-300">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-500" aria-hidden="true" />
            All configured models are compatible with your hardware and current.
          </div>
        ) : (
          <ul className="space-y-1">
            {errors.map((f) => (
              <FindingRow key={`${f.role}-${f.model}`} f={f} />
            ))}
            {others.map((f) => (
              <FindingRow key={`${f.role}-${f.model}`} f={f} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
