// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectGroup, SelectLabel, SelectSeparator } from "@/components/ui/select"
import { MODELS } from "@/lib/types"
import type { ModelCapabilities, ModelOption } from "@/lib/types"
import { fetchModelCatalog } from "@/lib/api/settings"
import { formatCost } from "@/lib/utils"
import { estimateTurnCost } from "@/lib/model-router"

interface ModelSelectProps {
  value: string
  onChange: (model: string) => void
  /**
   * Lowercased provider IDs that the user has configured an API key for
   * (registry names from GET /setup/status, e.g. ["openrouter", "openai"]).
   * Models whose ROUTING provider (the first segment of the model id,
   * e.g. "openrouter" in "openrouter/google/gemini-2.5-flash") isn't on
   * this list render disabled with a "Not configured" hint so the user
   * can't pick something that will error on send.
   *
   * When undefined we treat all providers as configured (back-compat: the
   * dropdown looks identical to its pre-Phase-7 behaviour).
   */
  configuredProviders?: string[]
}

function topCapability(caps: ModelCapabilities): string {
  const entries: [string, number][] = [
    ["code", caps.coding],
    ["reason", caps.reasoning],
    ["create", caps.creative],
    ["facts", caps.factual],
  ]
  entries.sort((a, b) => b[1] - a[1])
  return entries[0][0]
}

/** Preferred provider ordering — keep frontier vendors at the top of the
 *  dropdown. Anything not listed is appended in encounter order. */
const PROVIDER_ORDER = ["Anthropic", "OpenAI", "Google", "xAI", "Meta"]

function groupByProvider(models: ModelOption[]): Array<[string, ModelOption[]]> {
  const groups = new Map<string, ModelOption[]>()
  for (const m of models) {
    const list = groups.get(m.provider) ?? []
    list.push(m)
    groups.set(m.provider, list)
  }
  return [...groups.entries()].sort(([a], [b]) => {
    const ai = PROVIDER_ORDER.indexOf(a)
    const bi = PROVIDER_ORDER.indexOf(b)
    if (ai === -1 && bi === -1) return a.localeCompare(b)
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  })
}

/** Routing provider = first segment of the catalog id ("openrouter" in
 *  "openrouter/google/gemini-2.5-flash"). This — not the display brand —
 *  is what GET /setup/status lists in configured_providers. */
function routingProvider(id: string): string {
  return id.split("/")[0].toLowerCase()
}

export function ModelSelect({ value, onChange, configuredProviders }: ModelSelectProps) {
  const selectedModel = MODELS.find((m) => m.id === value)

  // Lowercased lookup set so callers can pass either casing.
  const configuredSet = useMemo(() => {
    if (!configuredProviders) return null
    return new Set(configuredProviders.map((p) => p.toLowerCase()))
  }, [configuredProviders])

  // Currently-dispatchable id set, validated against the live OpenRouter
  // catalog (E1 CR-004): a delisted id (absent from the catalog) is disabled +
  // "Unavailable" so a stale hardcoded catalog entry can't be selected. null =
  // catalog unknown (fetch failed / source "unavailable") → do NOT filter.
  const { data: modelCatalog } = useQuery({
    queryKey: ["models-catalog"],
    queryFn: () => fetchModelCatalog(),
    staleTime: 300_000,
  })
  const dispatchableSet = useMemo(() => {
    if (modelCatalog?.source !== "live_catalog" || !modelCatalog.ids.length) return null
    return new Set(modelCatalog.ids)
  }, [modelCatalog])

  const grouped = useMemo(() => groupByProvider(MODELS), [])

  // A model is usable when its ROUTING provider is configured (the P0-B
  // fix: catalog ids all route via "openrouter/...", while m.provider is a
  // display brand like "Google"/"Meta" that never appears in
  // configured_providers — brand-only matching permanently disabled those
  // rows). The brand fallback keeps direct-key setups (e.g. "anthropic")
  // enabled too.
  const isModelConfigured = (m: ModelOption): boolean =>
    configuredSet === null ||
    configuredSet.has(routingProvider(m.id)) ||
    configuredSet.has(m.provider.toLowerCase())

  // true/false when the live catalog is known; null = unknown (don't filter).
  const isModelDispatchable = (m: ModelOption): boolean | null =>
    dispatchableSet === null ? null : dispatchableSet.has(m.id)

  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-48">
        <span className="truncate">{selectedModel?.label ?? "Select model"}</span>
      </SelectTrigger>
      <SelectContent position="popper" className="min-w-[20rem]"> {/* drift-allowed: pinned popper min-width so model rows and their availability hints align */}
        {grouped.map(([provider, models], groupIdx) => {
          const groupConfigured = models.some(isModelConfigured)
          return (
            <SelectGroup key={provider}>
              {groupIdx > 0 && <SelectSeparator />}
              <SelectLabel className="flex items-center justify-between">
                <span>{provider}</span>
                {!groupConfigured && (
                  <span className="text-label-xxs font-normal text-muted-foreground/80">Not configured</span>
                )}
              </SelectLabel>
              {models.map((m) => {
                const cost = estimateTurnCost(m, 2000, 500)
                const top = m.capabilities ? topCapability(m.capabilities) : null
                const configured = isModelConfigured(m)
                const dispatchable = isModelDispatchable(m)
                // "Unavailable" comes only from the authoritative live OpenRouter
                // catalog — the old hand-maintained provider registry falsely
                // flagged most rows (CR-031).
                const unavailable = dispatchable === false
                return (
                  <SelectItem key={m.id} value={m.id} disabled={!configured || dispatchable === false}>
                    <span className="truncate">{m.label}</span>
                    {top && (
                      <span className="ml-1.5 shrink-0 rounded bg-muted px-1 py-0.5 text-label-xs text-muted-foreground">
                        {top}
                      </span>
                    )}
                    <span className="ml-1.5 shrink-0 text-label-xs text-muted-foreground">
                      ~{formatCost(cost)}
                    </span>
                    {unavailable && (
                      <span className="ml-1.5 shrink-0 rounded bg-muted px-1 py-0.5 text-label-xs text-muted-foreground/80">
                        Unavailable
                      </span>
                    )}
                  </SelectItem>
                )
              })}
            </SelectGroup>
          )
        })}
        <div className="border-t px-2 py-1.5 text-label-xxs text-muted-foreground/80">
          All models via OpenRouter. Non-US models accessible but not bundled by default.
        </div>
      </SelectContent>
    </Select>
  )
}
