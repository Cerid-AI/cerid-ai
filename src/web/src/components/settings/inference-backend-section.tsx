// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Cpu, Cloud, HardDrive, RefreshCw, Loader2, ExternalLink } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { fetchSystemCheck } from "@/lib/api"
import {
  backendOptionsForHardware,
  deriveRecommendation,
} from "@/lib/hardware-profile"
import { cn } from "@/lib/utils"
import type { RecommendedLocalBackend, SystemCheckResponse } from "@/lib/types"

const ICON_FOR_BACKEND = {
  quenchforge: Cpu,
  ollama: HardDrive,
  cloud: Cloud,
} as const

/**
 * Settings → Inference Backend.
 *
 * Surfaces the currently-detected hardware profile, the recommended backend,
 * and provides a "Re-detect" action that re-queries ``/system-check``. The
 * actual provider switch lives in the env (``INTERNAL_LLM_PROVIDER``) and is
 * not user-mutable from this view in PR 3 — wiring it through the settings
 * patch API is a PR 3b follow-up.
 */
export function InferenceBackendSection() {
  const [redetecting, setRedetecting] = useState(false)
  const { data, refetch, isLoading } = useQuery({
    queryKey: ["system-check"],
    queryFn: fetchSystemCheck,
    staleTime: 60_000,
    retry: 1,
  })

  const handleRedetect = useCallback(async () => {
    setRedetecting(true)
    try {
      await refetch()
    } finally {
      setRedetecting(false)
    }
  }, [refetch])

  if (isLoading || !data) {
    return (
      <section className="rounded-lg border bg-card p-4 space-y-3">
        <SectionHeader />
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Loading hardware profile…
        </div>
      </section>
    )
  }

  const { options, defaultId } = backendOptionsForHardware(data)
  const recommended: RecommendedLocalBackend =
    data.recommended_local_backend ?? deriveRecommendation(data)

  return (
    <section className="rounded-lg border bg-card p-4 space-y-3">
      <SectionHeader />

      <HardwareSummary system={data} />

      <div className="space-y-2">
        <p className="text-label-xs font-medium text-muted-foreground">
          Backend options
        </p>
        {options.map((opt) => {
          const Icon = ICON_FOR_BACKEND[opt.id]
          const isRecommended = opt.id === defaultId
          return (
            <div
              key={opt.id}
              className={cn(
                "flex items-start gap-3 rounded-lg border px-3 py-2.5",
                isRecommended ? "border-brand/40 bg-brand/5" : "bg-card",
              )}
            >
              <Icon
                className={cn(
                  "mt-0.5 h-4 w-4 shrink-0",
                  isRecommended ? "text-brand" : "text-muted-foreground",
                )}
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{opt.label}</span>
                  {opt.badge && (
                    <Badge variant="outline" className="border-brand/30 bg-brand/10 text-brand">
                      {opt.badge}
                    </Badge>
                  )}
                </div>
                <p className="mt-0.5 text-label-xs text-muted-foreground">
                  {opt.blurb}
                </p>
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex items-center justify-between border-t pt-3">
        <div className="space-y-0.5">
          <p className="text-label-xs text-muted-foreground">
            Active backend is controlled by{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono">
              INTERNAL_LLM_PROVIDER
            </code>{" "}
            in <code className="font-mono">.env</code>.
          </p>
          <p className="text-label-xs text-muted-foreground/80">
            Recommendation for your hardware:{" "}
            <span className="font-medium text-foreground">{recommended}</span>
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={handleRedetect}
          disabled={redetecting}
          className="shrink-0"
        >
          {redetecting ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="mr-1 h-3 w-3" />
          )}
          Re-detect
        </Button>
      </div>
    </section>
  )
}

function SectionHeader() {
  return (
    <div className="flex items-center justify-between">
      <div>
        <h3 className="text-sm font-semibold">Inference Backend</h3>
        <p className="text-label-xs text-muted-foreground">
          Where pipeline LLM calls (claim extraction, decomposition, contextual
          chunks) route.
        </p>
      </div>
      <a
        href="https://github.com/cerid-ai/quenchforge#readme"
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-1 text-label-xs text-brand hover:underline"
      >
        About Quenchforge
        <ExternalLink className="h-3 w-3" />
      </a>
    </div>
  )
}

function HardwareSummary({ system }: { system: SystemCheckResponse }) {
  return (
    <div className="grid gap-2 text-label-xs sm:grid-cols-2">
      <KV label="OS" value={system.os} />
      <KV label="CPU" value={system.cpu} />
      <KV label="GPU" value={system.gpu} />
      <KV
        label="Accel"
        value={system.gpu_acceleration ? system.gpu_acceleration : "—"}
      />
    </div>
  )
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border bg-muted/30 px-2 py-1">
      <span className="font-medium text-muted-foreground">{label}:</span>
      <span className="truncate text-foreground" title={value}>
        {value}
      </span>
    </div>
  )
}
