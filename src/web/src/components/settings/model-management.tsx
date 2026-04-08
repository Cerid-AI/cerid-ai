// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from "react"
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
// Inline Alert primitives (shadcn Alert component not installed in this project)
function Alert({ children, className, ...props }: React.HTMLAttributes<HTMLDivElement> & { variant?: string }) {
  return <div role="alert" className={`relative w-full rounded-lg border px-4 py-3 text-sm grid has-[>svg]:grid-cols-[calc(var(--spacing)*4)_1fr] grid-cols-[0_1fr] has-[>svg]:gap-x-3 gap-y-0.5 items-start [&>svg]:size-4 [&>svg]:translate-y-0.5 [&>svg]:text-current ${className ?? ""}`} {...props}>{children}</div>
}
function AlertDescription({ children, className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`text-muted-foreground col-start-2 text-sm [&_p]:leading-relaxed ${className ?? ""}`} {...props}>{children}</div>
}
import {
  Sparkles,
  RefreshCw,
  AlertTriangle,
  ArrowRight,
  X,
  Loader2,
  Clock,
  DollarSign,
  Layers,
} from "lucide-react"

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
            <Badge variant="secondary" className="bg-teal-500/10 text-teal-600 dark:text-teal-400 text-[10px] px-1.5 py-0">
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
        <p className="text-xs text-muted-foreground">{checkResult}</p>
      )}

      {/* Last checked */}
      {data?.last_checked && (
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Clock className="h-3 w-3" />
          Last checked: {new Date(data.last_checked).toLocaleString()}
          {data.catalog_size > 0 && (
            <span className="ml-1">({data.catalog_size} models in catalog)</span>
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
                <p className="text-[11px] text-muted-foreground">
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
              <Badge variant="outline" className="ml-1.5 text-[9px] px-1 py-0 border-amber-500/50">
                In use
              </Badge>
            )}
          </p>
          <p className="text-[11px] opacity-80">{reason}</p>
          {successor && (
            <p className="flex items-center gap-1 text-[11px]">
              <ArrowRight className="h-3 w-3" />
              Switch to <span className="font-mono text-[10px]">{successor}</span>
            </p>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 shrink-0 p-0 text-amber-600 hover:text-amber-700"
          onClick={() => onDismiss(item.update_id)}
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
          <Badge className="bg-teal-500/10 text-teal-600 dark:text-teal-400 text-[9px] px-1 py-0 border-0">
            New
          </Badge>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          {contextLength != null && <span>{(contextLength / 1000).toFixed(0)}K ctx</span>}
          {inputCost != null && outputCost != null && (
            <span>${inputCost.toFixed(2)} / ${outputCost.toFixed(2)} per 1M</span>
          )}
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-5 w-5 shrink-0 p-0 text-muted-foreground hover:text-foreground"
        onClick={() => onDismiss(item.update_id)}
      >
        <X className="h-3 w-3" />
      </Button>
    </div>
  )
}

function PriceChangeRow({
  item,
  onDismiss,
}: {
  item: ModelUpdateItem
  onDismiss: (id: string) => void
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0 flex-1">
        <span className="truncate text-xs">{item.model_id}</span>
        <Badge className="ml-1.5 bg-amber-500/10 text-amber-600 dark:text-amber-400 text-[9px] px-1 py-0 border-0">
          Price
        </Badge>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-5 w-5 shrink-0 p-0 text-muted-foreground hover:text-foreground"
        onClick={() => onDismiss(item.update_id)}
      >
        <X className="h-3 w-3" />
      </Button>
    </div>
  )
}
