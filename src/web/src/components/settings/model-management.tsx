// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  fetchModelUpdatesFull,
  triggerModelUpdateCheck,
  dismissModelUpdate,
  type ModelUpdateItem,
  type ModelUpdatesFullResponse,
} from "@/lib/api"
import { Card, CardContent, CardHeader, CardDescription } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import {
  Sparkles,
  RefreshCw,
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  ArrowDownRight,
  X,
  Loader2,
  Clock,
  DollarSign,
  Layers,
  CheckCircle2,
} from "lucide-react"

function formatRelativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const secs = Math.max(0, Math.floor(ms / 1000))
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins} min ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function formatCost(value: number): string {
  if (value === 0) return "free"
  if (value < 0.01) return `$${value.toFixed(4)}`
  return `$${value.toFixed(2)}`
}

export function ModelManagement() {
  const queryClient = useQueryClient()
  const [checking, setChecking] = useState(false)
  const [checkResult, setCheckResult] = useState<string | null>(null)

  const { data, isLoading } = useQuery<ModelUpdatesFullResponse>({
    queryKey: ["model-updates"],
    queryFn: fetchModelUpdatesFull,
    refetchInterval: 300_000, // 5 min
    staleTime: 120_000,
  })

  const updates = data?.updates ?? []
  const newModels = updates.filter((u) => u.update_type === "new")
  const deprecated = updates.filter((u) => u.update_type === "deprecated")
  const priceChanges = updates.filter((u) => u.update_type === "price_change")

  const handleCheck = async () => {
    setChecking(true)
    setCheckResult(null)
    try {
      const result = await triggerModelUpdateCheck()
      setCheckResult(
        `Found ${result.new_count} new, ${result.deprecated_count} deprecated models`,
      )
      queryClient.invalidateQueries({ queryKey: ["model-updates"] })
    } catch (e) {
      setCheckResult(e instanceof Error ? e.message : "Check failed")
    } finally {
      setChecking(false)
    }
  }

  const handleDismiss = async (updateId: string) => {
    try {
      await dismissModelUpdate(updateId)
      queryClient.invalidateQueries({ queryKey: ["model-updates"] })
    } catch {
      // silent
    }
  }

  return (
    <div className="space-y-3">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-teal-500" />
          <h3 className="text-sm font-medium">Model Management</h3>
          {updates.length > 0 && (
            <Badge variant="secondary" className="bg-teal-500/10 text-teal-600 dark:text-teal-400 text-label-xs px-1.5 py-0">
              {updates.length} update{updates.length !== 1 ? "s" : ""}
            </Badge>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleCheck}
          disabled={checking}
          className="h-7 text-xs"
        >
          {checking ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="mr-1 h-3 w-3" />
          )}
          Check for updates
        </Button>
      </div>

      {/* Check result */}
      {checkResult && (
        <Alert className="border-teal-500/30 bg-teal-500/5 [&>svg]:text-teal-500">
          <CheckCircle2 className="h-4 w-4" />
          <AlertDescription className="text-foreground">{checkResult}</AlertDescription>
        </Alert>
      )}

      {/* Last checked */}
      {data?.last_checked && (
        <div className="flex items-center gap-1.5 text-label-sm text-muted-foreground">
          <Clock className="h-3 w-3" />
          <span title={new Date(data.last_checked).toLocaleString()}>
            Last checked {formatRelativeTime(data.last_checked)}
          </span>
          {data.catalog_size > 0 && (
            <span className="ml-1">· {data.catalog_size} models in catalog</span>
          )}
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading model updates...
        </div>
      )}

      {/* Deprecated model warnings */}
      {deprecated.length > 0 && (
        <div className="space-y-2">
          {deprecated.map((item) => (
            <DeprecatedCard key={item.update_id} item={item} onDismiss={handleDismiss} />
          ))}
        </div>
      )}

      {/* New models */}
      {newModels.length > 0 && (
        <Card>
          <CardHeader className="px-4 pb-2 pt-3">
            <CardDescription className="flex items-center gap-1.5 text-xs">
              <Layers className="h-3.5 w-3.5 text-teal-500" />
              {newModels.length} new model{newModels.length !== 1 ? "s" : ""} available
            </CardDescription>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            <div className="space-y-2">
              {newModels.slice(0, 8).map((item) => (
                <NewModelRow key={item.update_id} item={item} onDismiss={handleDismiss} />
              ))}
              {newModels.length > 8 && (
                <p className="text-label-sm text-muted-foreground">
                  +{newModels.length - 8} more new models
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Price changes */}
      {priceChanges.length > 0 && (
        <Card>
          <CardHeader className="px-4 pb-2 pt-3">
            <CardDescription className="flex items-center gap-1.5 text-xs">
              <DollarSign className="h-3.5 w-3.5 text-amber-500" />
              {priceChanges.length} price change{priceChanges.length !== 1 ? "s" : ""}
            </CardDescription>
          </CardHeader>
          <CardContent className="px-4 pb-3">
            <div className="space-y-2">
              {priceChanges.map((item) => (
                <PriceChangeRow key={item.update_id} item={item} onDismiss={handleDismiss} />
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {!isLoading && updates.length === 0 && (
        <p className="py-2 text-xs text-muted-foreground">
          No pending model updates. Click &quot;Check for updates&quot; to scan the OpenRouter catalog.
        </p>
      )}
    </div>
  )
}

function DeprecatedCard({
  item,
  onDismiss,
}: {
  item: ModelUpdateItem
  onDismiss: (id: string) => void
}) {
  const successor = item.details.successor as string | undefined
  const reason = (item.details.reason as string) ?? "Deprecated"
  const inUse = item.details.in_use as boolean | undefined

  return (
    <Alert
      variant="destructive"
      className="border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-400 [&>svg]:text-amber-500"
    >
      <AlertTriangle className="h-4 w-4" />
      <AlertDescription className="flex items-start justify-between gap-2">
        <div className="space-y-1">
          <p className="text-xs font-medium">
            {item.model_id}
            {inUse && (
              <Badge variant="outline" className="ml-1.5 text-label-xxs px-1 py-0 border-amber-500/50">
                In use
              </Badge>
            )}
          </p>
          <p className="text-label-sm opacity-80">{reason}</p>
          {successor && (
            <p className="flex items-center gap-1 text-label-sm">
              <ArrowRight className="h-3 w-3" />
              Switch to <span className="font-mono text-label-xs">{successor}</span>
            </p>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 shrink-0 p-0 text-amber-600 hover:text-amber-700"
          onClick={() => onDismiss(item.update_id)}
          aria-label="Dismiss deprecation warning"
        >
          <X className="h-3 w-3" />
        </Button>
      </AlertDescription>
    </Alert>
  )
}

function NewModelRow({
  item,
  onDismiss,
}: {
  item: ModelUpdateItem
  onDismiss: (id: string) => void
}) {
  const name = (item.details.name as string) ?? item.model_id
  const inputCost = item.details.input_cost as number | undefined
  const outputCost = item.details.output_cost as number | undefined
  const contextLength = item.details.context_length as number | undefined

  return (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-xs font-medium">{name}</span>
          <Badge className="bg-teal-500/10 text-teal-600 dark:text-teal-400 text-label-xxs px-1 py-0 border-0">
            New
          </Badge>
        </div>
        <div className="flex items-center gap-2 text-label-xs text-muted-foreground">
          {contextLength != null && <span>{(contextLength / 1000).toFixed(0)}K ctx</span>}
          {inputCost != null && outputCost != null && (
            <span className="tabular-nums">
              in {formatCost(inputCost)} · out {formatCost(outputCost)} / 1M tok
            </span>
          )}
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-5 w-5 shrink-0 p-0 text-muted-foreground hover:text-foreground"
        onClick={() => onDismiss(item.update_id)}
        aria-label={`Dismiss new model notification for ${name}`}
      >
        <X className="h-3 w-3" />
      </Button>
    </div>
  )
}

interface CostDelta {
  label: string
  prev: number
  next: number
  direction: "up" | "down" | "flat"
}

function buildCostDeltas(details: Record<string, unknown>): CostDelta[] {
  const out: CostDelta[] = []
  const pairs: Array<[string, string, string]> = [
    ["old_input_cost", "new_input_cost", "in"],
    ["old_output_cost", "new_output_cost", "out"],
  ]
  for (const [oldKey, newKey, label] of pairs) {
    const prev = details[oldKey] as number | undefined
    const next = details[newKey] as number | undefined
    if (prev == null || next == null) continue
    const direction = next > prev ? "up" : next < prev ? "down" : "flat"
    out.push({ label, prev, next, direction })
  }
  return out
}

function PriceChangeRow({
  item,
  onDismiss,
}: {
  item: ModelUpdateItem
  onDismiss: (id: string) => void
}) {
  const deltas = buildCostDeltas(item.details)
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-xs">{item.model_id}</span>
          <Badge className="bg-amber-500/10 text-amber-600 dark:text-amber-400 text-label-xxs px-1 py-0 border-0">
            Price
          </Badge>
        </div>
        {deltas.length === 0 ? (
          <p className="text-label-xs text-muted-foreground">
            Pricing was updated upstream.
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-label-xs tabular-nums text-muted-foreground">
            {deltas.map((d) => {
              const colorClass =
                d.direction === "up"
                  ? "text-red-500"
                  : d.direction === "down"
                    ? "text-emerald-500"
                    : "text-muted-foreground"
              const Arrow =
                d.direction === "up"
                  ? ArrowUpRight
                  : d.direction === "down"
                    ? ArrowDownRight
                    : ArrowRight
              return (
                <span key={d.label} className="inline-flex items-center gap-1">
                  <span className="text-muted-foreground/70">{d.label}</span>
                  <span>{formatCost(d.prev)}</span>
                  <Arrow className={`h-3 w-3 ${colorClass}`} aria-hidden="true" />
                  <span className={colorClass}>{formatCost(d.next)}</span>
                </span>
              )
            })}
          </div>
        )}
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-5 w-5 shrink-0 p-0 text-muted-foreground hover:text-foreground"
        onClick={() => onDismiss(item.update_id)}
        aria-label={`Dismiss price change notification for ${item.model_id}`}
      >
        <X className="h-3 w-3" />
      </Button>
    </div>
  )
}
