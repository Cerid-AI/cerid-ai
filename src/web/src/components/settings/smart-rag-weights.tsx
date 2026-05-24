// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Custom Smart RAG — per-source weight tuning panel (Phase I Day 3).
//
// Renders inside Settings → Pipeline as a Pro-gated section. Free
// users see the full slider list with a faded overlay + upgrade CTA;
// Pro users get the live editor.
//
// Save semantics: clicking "Save" issues one PUT with the full
// override map, capturing every source that's been moved off 1.0.
// Reset clears all weights server-side.

import { useCallback, useEffect, useMemo, useState } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import {
  AlertCircle,
  Database,
  Globe,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
} from "lucide-react"
import { cn } from "@/lib/utils"
import {
  fetchRagSources,
  fetchRagWeights,
  putRagWeights,
  resetRagWeights,
  type RagSource,
} from "@/lib/api/settings"

const STEP = 0.1
const ABS_EQ = 1e-9

interface SmartRagWeightsProps {
  tier?: string  // "community" | "pro" — when "community", lock the editor
}

export function SmartRagWeights({ tier = "community" }: SmartRagWeightsProps) {
  const [sources, setSources] = useState<RagSource[] | null>(null)
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const [min, setMin] = useState(0.0)
  const [max, setMax] = useState(2.0)
  const [defaultWeight, setDefaultWeight] = useState(1.0)
  const [featureEnabled, setFeatureEnabled] = useState(false)
  const [busy, setBusy] = useState<"loading" | "saving" | "resetting" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastSaved, setLastSaved] = useState<number | null>(null)

  const isPro = tier !== "community"
  const locked = !isPro || !featureEnabled

  const refresh = useCallback(async () => {
    setBusy("loading")
    setError(null)
    try {
      const [sourcesResp, weightsResp] = await Promise.all([
        fetchRagSources(),
        fetchRagWeights(),
      ])
      setSources(sourcesResp.sources)
      setMin(sourcesResp.min_weight)
      setMax(sourcesResp.max_weight)
      setDefaultWeight(sourcesResp.default_weight)
      setFeatureEnabled(sourcesResp.feature_enabled)
      // Seed overrides from server-stored weights (so unmoved sliders
      // still reflect saved state).
      setOverrides(weightsResp.weights)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load")
    } finally {
      setBusy(null)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    refresh()
  }, [refresh])

  // Effective weight for a source = override if set, else stored
  // current_weight (from server), else default.
  const effectiveWeight = useCallback((s: RagSource): number => {
    if (s.name in overrides) return overrides[s.name]
    return s.current_weight
  }, [overrides])

  const dirty = useMemo(() => {
    if (!sources) return false
    return sources.some((s) => {
      const eff = effectiveWeight(s)
      return Math.abs(eff - s.current_weight) > ABS_EQ
    })
  }, [sources, effectiveWeight])

  const handleChange = useCallback((name: string, value: number) => {
    if (locked) return
    setOverrides((prev) => ({ ...prev, [name]: value }))
  }, [locked])

  const handleSave = useCallback(async () => {
    if (!dirty) return
    setBusy("saving")
    setError(null)
    try {
      // POST every override that differs from default — saves Redis
      // hash bytes for the common "all at 1.0" case.
      const payload: Record<string, number> = {}
      for (const [name, value] of Object.entries(overrides)) {
        if (Math.abs(value - defaultWeight) > ABS_EQ) {
          payload[name] = value
        }
      }
      const resp = await putRagWeights(payload)
      setOverrides(resp.weights)
      setLastSaved(Date.now())
      await refresh()  // re-pull authoritative state
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed")
    } finally {
      setBusy(null)
    }
  }, [dirty, overrides, defaultWeight, refresh])

  const handleReset = useCallback(async () => {
    setBusy("resetting")
    setError(null)
    try {
      await resetRagWeights()
      setOverrides({})
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reset failed")
    } finally {
      setBusy(null)
    }
  }, [refresh])

  // Recall impact estimate is a heuristic: average of (eff - default)
  // across non-default weights. Positive = recall boost projected;
  // negative = ranking demotion. Surfaces as a directional hint, not
  // a guarantee — true recall impact depends on the query distribution.
  const recallImpact = useMemo(() => {
    if (!sources) return 0
    const deltas = sources
      .map((s) => effectiveWeight(s) - defaultWeight)
      .filter((d) => Math.abs(d) > ABS_EQ)
    if (deltas.length === 0) return 0
    const avg = deltas.reduce((a, b) => a + b, 0) / deltas.length
    return Math.round(avg * 25)  // crude % approximation
  }, [sources, effectiveWeight, defaultWeight])

  if (sources === null) {
    if (error) {
      return (
        <Card className="p-4">
          <div
            className="text-sm text-red-500 p-2 rounded border border-red-500/30 bg-red-500/5"
            role="alert"
          >
            <AlertCircle className="w-4 h-4 inline mr-1" />
            {error}
          </div>
        </Card>
      )
    }
    return (
      <Card className="p-6 flex items-center justify-center text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin mr-2" />
        Loading retrieval sources…
      </Card>
    )
  }

  return (
    <Card
      className={cn(
        "p-4 space-y-4",
        locked && "relative overflow-hidden",
      )}
      data-testid="smart-rag-weights"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-amber-500" />
            Custom Smart RAG
            {!isPro && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-600">
                Pro
              </span>
            )}
          </h3>
          <p className="text-xs text-muted-foreground mt-1">
            Adjust per-source weights to boost or demote how each source
            influences retrieval rankings. Default 1.0 = no change.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={refresh}
            disabled={busy !== null}
            aria-label="Refresh sources"
            data-testid="smart-rag-refresh"
          >
            <RefreshCw className={cn("w-4 h-4", busy === "loading" && "animate-spin")} />
          </Button>
        </div>
      </div>

      {error && (
        <div
          className="text-sm text-red-500 p-2 rounded border border-red-500/30 bg-red-500/5"
          role="alert"
        >
          <AlertCircle className="w-4 h-4 inline mr-1" />
          {error}
        </div>
      )}

      <div className="space-y-2 max-h-96 overflow-y-auto">
        {sources.map((s) => {
          const eff = effectiveWeight(s)
          const Icon = s.kind === "data_source" ? Globe : Database
          return (
            <div
              key={s.name}
              className={cn(
                "flex items-center gap-3 py-1.5 px-2 rounded hover:bg-muted/50",
              )}
              data-testid={`smart-rag-row-${s.name}`}
            >
              <Icon className="w-4 h-4 text-muted-foreground flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{s.name}</div>
                <div className="text-xs text-muted-foreground truncate">
                  {s.description}
                </div>
              </div>
              <input
                type="range"
                min={min}
                max={max}
                step={STEP}
                value={eff}
                onChange={(e) => handleChange(s.name, parseFloat(e.target.value))}
                disabled={locked || busy === "saving"}
                className="w-32 accent-amber-500"
                aria-label={`Weight for ${s.name}`}
                data-testid={`smart-rag-slider-${s.name}`}
              />
              <span
                className={cn(
                  "text-xs font-mono tabular-nums w-10 text-right",
                  Math.abs(eff - defaultWeight) < ABS_EQ
                    ? "text-muted-foreground"
                    : eff > defaultWeight
                      ? "text-green-600"
                      : "text-amber-600",
                )}
              >
                {eff.toFixed(1)}
              </span>
            </div>
          )
        })}
      </div>

      <div className="flex items-center justify-between pt-2 border-t">
        <span className="text-xs text-muted-foreground">
          {dirty && (
            <>
              Estimated recall impact:{" "}
              <span
                className={cn(
                  recallImpact > 0 && "text-green-600",
                  recallImpact < 0 && "text-amber-600",
                )}
              >
                {recallImpact > 0 ? "+" : ""}
                {recallImpact}%
              </span>
            </>
          )}
          {!dirty && lastSaved && (
            <>Last saved {new Date(lastSaved).toLocaleTimeString()}</>
          )}
        </span>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleReset}
            disabled={locked || busy !== null}
            data-testid="smart-rag-reset"
          >
            Reset all
          </Button>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={locked || !dirty || busy !== null}
            data-testid="smart-rag-save"
          >
            {busy === "saving" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <>
                <Save className="w-4 h-4 mr-1" />
                Save
              </>
            )}
          </Button>
        </div>
      </div>

      {locked && (
        <div
          className="absolute inset-0 flex items-center justify-center bg-background/60 backdrop-blur-sm"
          data-testid="smart-rag-locked-overlay"
        >
          <div className="text-center p-4">
            <Sparkles className="w-8 h-8 mx-auto text-amber-500 mb-2" />
            <h4 className="text-sm font-semibold">Custom Smart RAG is a Pro feature</h4>
            <p className="text-xs text-muted-foreground mt-1 max-w-xs">
              Upgrade to fine-tune retrieval weights for every source.
              Boost what matters; demote what doesn't.
            </p>
          </div>
        </div>
      )}
    </Card>
  )
}
