// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  SettingRow,
  AdvancedDisclosure,
  ConfirmActionButton,
  SliderRowPair,
  SliderRow,
} from "../settings-primitives"
import { getDef } from "@/lib/settings-registry"
import {
  fetchRagWeights,
  fetchRagSources,
  putRagWeights,
  resetRagWeights,
} from "@/lib/api/settings"
import type { RagSource } from "@/lib/api/settings"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useNavigation } from "@/contexts/navigation-context"
import type { SettingsCategoryPageProps } from "./page-props"
import type { SettingsUpdate } from "@/lib/types"

// ── Smart RAG weights panel ───────────────────────────────────────────────────

function SmartRagPanel() {
  const { forFlag } = useEntitlements()
  const { goTo } = useNavigation()
  const entitlement = forFlag("custom_smart_rag", "pro")
  const locked = entitlement.state === "locked"
  const flagOff = entitlement.state === "flag-off"

  const [sources, setSources] = useState<RagSource[] | null>(null)
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const [minW, setMinW] = useState(0.0)
  const [maxW, setMaxW] = useState(2.0)
  const [defaultWeight, setDefaultWeight] = useState(1.0)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const refresh = useCallback(async () => {
    setLoadError(null)
    try {
      const [sr, wr] = await Promise.all([fetchRagSources(), fetchRagWeights()])
      setSources(sr.sources)
      setMinW(sr.min_weight)
      setMaxW(sr.max_weight)
      setDefaultWeight(sr.default_weight)
      setOverrides(wr.weights)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load Smart RAG sources")
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (fetch/subscription); behavior validated in tests
    refresh()
  }, [refresh])

  const effectiveWeight = (s: RagSource): number =>
    s.name in overrides ? overrides[s.name] : s.current_weight

  const handleChange = (name: string, value: number) => {
    if (locked || flagOff) return
    setOverrides((prev) => ({ ...prev, [name]: value }))
  }

  const dirty = sources?.some((s) => Math.abs(effectiveWeight(s) - s.current_weight) > 1e-9) ?? false

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const payload: Record<string, number> = {}
      for (const [name, value] of Object.entries(overrides)) {
        if (Math.abs(value - defaultWeight) > 1e-9) payload[name] = value
      }
      await putRagWeights(payload)
      await refresh()
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Save failed")
    }
    setSaving(false)
  }

  const weightsDef = getDef("retrieval.smartRag.weights")
  const resetDef = getDef("retrieval.smartRag.reset")

  if (sources === null && !loadError) {
    return (
      <div className="flex items-center gap-2 text-label-sm text-muted-foreground py-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Loading sources…
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {loadError && (
        <p role="alert" className="text-label-xs text-destructive">{loadError}</p>
      )}

      {locked && (
        <div className="rounded-md border p-3 space-y-1.5">
          <p className="text-label-sm font-medium">Custom Smart RAG requires the Pro plan</p>
          <p className="text-label-xs text-muted-foreground">
            Upgrade to fine-tune per-source retrieval weights.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => goTo("settings", { category: "plan" })}
          >
            View plan
          </Button>
        </div>
      )}

      {!locked && flagOff && (
        <p className="text-label-xs text-muted-foreground">
          Custom Smart RAG is disabled on this server (feature flag: custom_smart_rag).
        </p>
      )}

      {sources !== null && sources.length > 0 && (
        <div className="space-y-1.5">
          {sources.map((s) => {
            const eff = effectiveWeight(s)
            return (
              <div
                key={s.name}
                className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-muted/40"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate">{s.name}</p>
                  <p className="text-label-xs text-muted-foreground truncate">{s.description}</p>
                </div>
                <Slider
                  aria-label={`Weight for ${s.name}`}
                  value={[eff]}
                  onValueChange={([v]) => handleChange(s.name, v)}
                  min={minW}
                  max={maxW}
                  step={0.1}
                  disabled={locked || flagOff || saving}
                  className="w-28"
                />
                <span className="w-8 text-right font-mono text-label-xs tabular-nums text-muted-foreground">
                  {eff.toFixed(1)}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {saveError && (
        <p role="alert" className="text-label-xs text-destructive">{saveError}</p>
      )}

      <div className="flex items-center gap-2 pt-1">
        {resetDef && (
          <ConfirmActionButton
            danger="confirm"
            title="Reset all Smart RAG weights?"
            description="Resets every source weight to 1.0 (default)."
            actionLabel="Reset"
            onConfirm={async () => {
              await resetRagWeights()
              setOverrides({})
              await refresh()
            }}
            variant="outline"
            size="sm"
            disabled={locked || flagOff}
          >
            Reset all
          </ConfirmActionButton>
        )}
        {weightsDef && (
          <Button
            size="sm"
            disabled={locked || flagOff || !dirty || saving}
            onClick={handleSave}
          >
            {saving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            Save weights
          </Button>
        )}
      </div>
    </div>
  )
}

// ── Hybrid weights pair ───────────────────────────────────────────────────────

function HybridWeightsPair({ settings, patch }: Pick<SettingsCategoryPageProps, "settings" | "patch">) {
  const [localVec, setLocalVec] = useState(settings.hybrid_vector_weight ?? 0.5)
  const [localKw, setLocalKw] = useState(settings.hybrid_keyword_weight ?? 0.5)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (server settings refresh); behavior validated in tests
    setLocalVec(settings.hybrid_vector_weight ?? 0.5)
    setLocalKw(settings.hybrid_keyword_weight ?? 0.5)
  }, [settings.hybrid_vector_weight, settings.hybrid_keyword_weight])

  const commit = useCallback((vec: number, kw: number) => {
    void patch({ hybrid_vector_weight: vec, hybrid_keyword_weight: kw })
  }, [patch])

  const vecDef = getDef("retrieval.hybridWeights.vectorWeight")
  const kwDef = getDef("retrieval.hybridWeights.keywordWeight")
  if (!vecDef || !kwDef) return null

  return (
    <SliderRowPair
      a={{
        label: vecDef.label,
        value: localVec,
        onChange: (v) => { setLocalVec(v); commit(v, localKw) },
        info: vecDef.helpText,
      }}
      b={{
        label: kwDef.label,
        value: localKw,
        onChange: (v) => { setLocalKw(v); commit(localVec, v) },
        info: kwDef.helpText,
      }}
      sumLabel="Combined hybrid weight"
      warning="Both weights are near zero — very few results will be returned."
    />
  )
}

function RerankWeightsPair({ settings, patch }: Pick<SettingsCategoryPageProps, "settings" | "patch">) {
  const [localLlm, setLocalLlm] = useState(settings.rerank_llm_weight ?? 0.5)
  const [localOrig, setLocalOrig] = useState(settings.rerank_original_weight ?? 0.5)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (server settings refresh); behavior validated in tests
    setLocalLlm(settings.rerank_llm_weight ?? 0.5)
    setLocalOrig(settings.rerank_original_weight ?? 0.5)
  }, [settings.rerank_llm_weight, settings.rerank_original_weight])

  const commit = useCallback((llm: number, orig: number) => {
    void patch({ rerank_llm_weight: llm, rerank_original_weight: orig })
  }, [patch])

  const llmDef = getDef("retrieval.hybridWeights.rerankLlmWeight")
  const origDef = getDef("retrieval.hybridWeights.rerankOriginalWeight")
  if (!llmDef || !origDef) return null

  return (
    <SliderRowPair
      a={{
        label: llmDef.label,
        value: localLlm,
        onChange: (v) => { setLocalLlm(v); commit(v, localOrig) },
        info: llmDef.helpText,
      }}
      b={{
        label: origDef.label,
        value: localOrig,
        onChange: (v) => { setLocalOrig(v); commit(localLlm, v) },
        info: origDef.helpText,
      }}
      sumLabel="Combined rerank weight"
      warning="Both rerank weights are near zero — reranking will have minimal effect."
    />
  )
}

// ── Knowledge-pack relevance weight (Slice 7.2) ───────────────────────────────

function PackRelevanceWeightRow({ settings, patch }: Pick<SettingsCategoryPageProps, "settings" | "patch">) {
  const [local, setLocal] = useState(settings.pack_relevance_weight ?? 0.7)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (server settings refresh)
    setLocal(settings.pack_relevance_weight ?? 0.7)
  }, [settings.pack_relevance_weight])

  const def = getDef("retrieval.hybridWeights.packRelevanceWeight")
  if (!def) return null

  return (
    <SettingRow def={def}>
      <SliderRow
        label={def.label}
        value={local}
        onChange={(v) => { setLocal(v); void patch({ pack_relevance_weight: v }) }}
        min={0}
        max={2}
        step={0.05}
        info={def.helpText}
        recommended="Recommended: 0.7 (personal-first)"
      />
    </SettingRow>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RetrievalAnswersCategory({ settings, patch }: SettingsCategoryPageProps) {
  return (
    <div className="density-stack">

      {/* Context injection */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Context Injection</h3>

          {(() => {
            const modeDef = getDef("retrieval.contextInjection.ragMode")
            if (!modeDef) return null
            return (
              <SettingRow def={modeDef}>
                <Select
                  value={settings.rag_mode ?? "smart"}
                  onValueChange={(v) => void patch({ rag_mode: v })}
                >
                  <SelectTrigger className="h-8 w-36 text-sm" aria-label={modeDef.label}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(modeDef.options ?? []).map((o) => (
                      <SelectItem key={String(o.value)} value={String(o.value)}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </SettingRow>
            )
          })()}

          {(() => {
            const def = getDef("retrieval.contextInjection.autoInject")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <Switch
                  size="sm"
                  aria-label={def.label}
                  checked={settings.enable_auto_inject ?? true}
                  onCheckedChange={(v) => void patch({ enable_auto_inject: v })}
                />
              </SettingRow>
            )
          })()}

          {(settings.enable_auto_inject ?? true) && (() => {
            const def = getDef("retrieval.contextInjection.threshold")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <SliderRow
                  label="Threshold"
                  value={settings.auto_inject_threshold ?? 0.55}
                  onChange={(v) => void patch({ auto_inject_threshold: v })}
                  min={0}
                  max={1}
                  step={0.01}
                  info={def.helpText}
                />
              </SettingRow>
            )
          })()}
        </CardContent>
      </Card>

      {/* Answer quality */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Answer Quality</h3>

          {(() => {
            const def = getDef("retrieval.answerQuality.selfRag")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <Switch
                  size="sm"
                  aria-label={def.label}
                  checked={settings.enable_self_rag ?? true}
                  onCheckedChange={(v) => void patch({ enable_self_rag: v })}
                />
              </SettingRow>
            )
          })()}

          {(() => {
            const def = getDef("retrieval.answerQuality.hallucinationCheck")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <Switch
                  size="sm"
                  aria-label={def.label}
                  checked={settings.enable_hallucination_check ?? true}
                  onCheckedChange={(v) => void patch({ enable_hallucination_check: v })}
                />
              </SettingRow>
            )
          })()}

          {(settings.enable_hallucination_check ?? true) && (() => {
            const def = getDef("retrieval.answerQuality.hallucinationThreshold")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <SliderRow
                  label="Threshold"
                  value={settings.hallucination_threshold ?? 0.7}
                  onChange={(v) => void patch({ hallucination_threshold: v })}
                  min={0}
                  max={1}
                  step={0.01}
                  info={def.helpText}
                />
              </SettingRow>
            )
          })()}
        </CardContent>
      </Card>

      {/* Learning */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Learning</h3>

          {(() => {
            const def = getDef("retrieval.learning.feedbackLoop")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <Switch
                  size="sm"
                  aria-label={def.label}
                  checked={settings.enable_feedback_loop ?? true}
                  onCheckedChange={(v) => void patch({ enable_feedback_loop: v })}
                />
              </SettingRow>
            )
          })()}

          {(() => {
            const def = getDef("retrieval.learning.memoryExtraction")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <Switch
                  size="sm"
                  aria-label={def.label}
                  checked={settings.enable_memory_extraction ?? true}
                  onCheckedChange={(v) => void patch({ enable_memory_extraction: v })}
                />
              </SettingRow>
            )
          })()}
        </CardContent>
      </Card>

      {/* Hybrid weights */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Hybrid Search Weights</h3>
          <HybridWeightsPair settings={settings} patch={patch} />

          <AdvancedDisclosure category="retrieval" group="hybridWeights">
            <RerankWeightsPair settings={settings} patch={patch} />
            <PackRelevanceWeightRow settings={settings} patch={patch} />
            {(() => {
              const htDef = getDef("retrieval.hybridWeights.temporalHalfLife")
              const rwDef = getDef("retrieval.hybridWeights.recencyWeight")
              return (
                <>
                  {htDef && <SettingRow def={htDef} />}
                  {rwDef && <SettingRow def={rwDef} />}
                </>
              )
            })()}
          </AdvancedDisclosure>
        </CardContent>
      </Card>

      {/* Smart RAG */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Smart RAG</h3>
          <SmartRagPanel />
        </CardContent>
      </Card>

      {/* Pipeline stages — all advanced */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Pipeline Stages</h3>
          <AdvancedDisclosure category="retrieval" group="pipelineStages">
            <PipelineStageControls settings={settings} patch={patch} />
          </AdvancedDisclosure>
        </CardContent>
      </Card>

    </div>
  )
}

// ── Pipeline stage controls ───────────────────────────────────────────────────

function PipelineStageControls({ settings, patch }: Pick<SettingsCategoryPageProps, "settings" | "patch">) {
  return (
    <div className="space-y-4">

      {/* Adaptive retrieval */}
      {(() => {
        const def = getDef("retrieval.pipelineStages.adaptiveRetrieval")
        if (!def) return null
        return (
          <SettingRow def={def}>
            <Switch
              size="sm"
              aria-label={def.label}
              checked={settings.enable_adaptive_retrieval ?? true}
              onCheckedChange={(v) => void patch({ enable_adaptive_retrieval: v })}
            />
          </SettingRow>
        )
      })()}

      {(settings.enable_adaptive_retrieval ?? true) && (() => {
        const def = getDef("retrieval.pipelineStages.adaptiveLightTopK")
        if (!def) return null
        return (
          <SettingRow def={def}>
            <SliderRow
              label="Light top-K"
              value={settings.adaptive_retrieval_light_top_k ?? 3}
              onChange={(v) => void patch({ adaptive_retrieval_light_top_k: Math.round(v) })}
              min={1}
              max={10}
              step={1}
              info={def.helpText}
            />
          </SettingRow>
        )
      })()}

      {/* Sparse retrieval + fusion mode */}
      {(() => {
        const sparseDef = getDef("retrieval.pipelineStages.sparseRetrieval")
        const fusionDef = getDef("retrieval.pipelineStages.fusionMode")
        const sparseWeightDef = getDef("retrieval.pipelineStages.sparseWeight")
        if (!sparseDef || !fusionDef) return null

        const sparseEnabled = settings.enable_sparse_retrieval ?? false

        return (
          <>
            <SettingRow def={sparseDef}>
              <div className="space-y-0.5">
                <Switch
                  size="sm"
                  aria-label={sparseDef.label}
                  checked={sparseEnabled}
                  onCheckedChange={(v) => {
                    const update: SettingsUpdate = { enable_sparse_retrieval: v }
                    if (v) update.hybrid_fusion_mode = "tri_rrf"
                    void patch(update)
                  }}
                />
                {sparseEnabled && (
                  <p className="text-label-xxs text-muted-foreground">
                    Enabling sets fusion to tri-RRF
                  </p>
                )}
              </div>
            </SettingRow>

            <SettingRow def={fusionDef}>
              <Select
                value={settings.hybrid_fusion_mode ?? "weighted_sum"}
                onValueChange={(v) => void patch({ hybrid_fusion_mode: v as "weighted_sum" | "rrf" | "tri_rrf" })}
              >
                <SelectTrigger className="h-8 w-40 text-sm" aria-label={fusionDef.label}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(fusionDef.options ?? []).map((o) => (
                    <SelectItem key={String(o.value)} value={String(o.value)}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </SettingRow>

            {sparseEnabled && sparseWeightDef && (
              <SettingRow def={sparseWeightDef}>
                <SliderRow
                  label="Sparse weight"
                  value={settings.hybrid_rrf_sparse_weight ?? 1.0}
                  onChange={(v) => void patch({ hybrid_rrf_sparse_weight: v })}
                  min={0}
                  max={5}
                  step={0.1}
                  info={sparseWeightDef.helpText}
                />
              </SettingRow>
            )}
          </>
        )
      })()}

      {/* Query decomposition */}
      {(() => {
        const def = getDef("retrieval.pipelineStages.queryDecomposition")
        const maxDef = getDef("retrieval.pipelineStages.maxSubqueries")
        if (!def) return null
        return (
          <>
            <SettingRow def={def}>
              <Switch
                size="sm"
                aria-label={def.label}
                checked={settings.enable_query_decomposition ?? false}
                onCheckedChange={(v) => void patch({ enable_query_decomposition: v })}
              />
            </SettingRow>
            {(settings.enable_query_decomposition ?? false) && maxDef && (
              <SettingRow def={maxDef}>
                <SliderRow
                  label="Max sub-queries"
                  value={settings.query_decomposition_max_subqueries ?? 4}
                  onChange={(v) => void patch({ query_decomposition_max_subqueries: Math.round(v) })}
                  min={2}
                  max={8}
                  step={1}
                  info={maxDef.helpText}
                />
              </SettingRow>
            )}
          </>
        )
      })()}

      {/* MMR diversity */}
      {(() => {
        const def = getDef("retrieval.pipelineStages.mmrDiversity")
        const lambdaDef = getDef("retrieval.pipelineStages.mmrLambda")
        if (!def) return null
        return (
          <>
            <SettingRow def={def}>
              <Switch
                size="sm"
                aria-label={def.label}
                checked={settings.enable_mmr_diversity ?? false}
                onCheckedChange={(v) => void patch({ enable_mmr_diversity: v })}
              />
            </SettingRow>
            {(settings.enable_mmr_diversity ?? false) && lambdaDef && (
              <SettingRow def={lambdaDef}>
                <SliderRow
                  label="λ"
                  value={settings.mmr_lambda ?? 0.5}
                  onChange={(v) => void patch({ mmr_lambda: v })}
                  min={0}
                  max={1}
                  step={0.05}
                  info={lambdaDef.helpText}
                />
              </SettingRow>
            )}
          </>
        )
      })()}

      {/* Intelligent assembly */}
      {(() => {
        const def = getDef("retrieval.pipelineStages.intelligentAssembly")
        if (!def) return null
        return (
          <SettingRow def={def}>
            <Switch
              size="sm"
              aria-label={def.label}
              checked={settings.enable_intelligent_assembly ?? false}
              onCheckedChange={(v) => void patch({ enable_intelligent_assembly: v })}
            />
          </SettingRow>
        )
      })()}

      {/* Late interaction */}
      {(() => {
        const def = getDef("retrieval.pipelineStages.lateInteraction")
        const topNDef = getDef("retrieval.pipelineStages.lateInteractionTopN")
        const blendDef = getDef("retrieval.pipelineStages.lateInteractionBlend")
        if (!def) return null
        return (
          <>
            <SettingRow def={def}>
              <Switch
                size="sm"
                aria-label={def.label}
                checked={settings.enable_late_interaction ?? false}
                onCheckedChange={(v) => void patch({ enable_late_interaction: v })}
              />
            </SettingRow>
            {(settings.enable_late_interaction ?? false) && (
              <>
                {topNDef && (
                  <SettingRow def={topNDef}>
                    <SliderRow
                      label="Candidates"
                      value={settings.late_interaction_top_n ?? 8}
                      onChange={(v) => void patch({ late_interaction_top_n: Math.round(v) })}
                      min={2}
                      max={20}
                      step={1}
                      info={topNDef.helpText}
                    />
                  </SettingRow>
                )}
                {blendDef && (
                  <SettingRow def={blendDef}>
                    <SliderRow
                      label="Blend weight"
                      value={settings.late_interaction_blend_weight ?? 0.2}
                      onChange={(v) => void patch({ late_interaction_blend_weight: v })}
                      min={0}
                      max={0.5}
                      step={0.01}
                      info={blendDef.helpText}
                    />
                  </SettingRow>
                )}
              </>
            )}
          </>
        )
      })()}

      {/* Semantic cache */}
      {(() => {
        const def = getDef("retrieval.pipelineStages.semanticCache")
        const threshDef = getDef("retrieval.pipelineStages.semanticCacheThreshold")
        if (!def) return null
        return (
          <>
            <SettingRow def={def}>
              <Switch
                size="sm"
                aria-label={def.label}
                checked={settings.enable_semantic_cache ?? true}
                onCheckedChange={(v) => void patch({ enable_semantic_cache: v })}
              />
            </SettingRow>
            {(settings.enable_semantic_cache ?? true) && threshDef && (
              <SettingRow def={threshDef}>
                <SliderRow
                  label="Threshold"
                  value={settings.semantic_cache_threshold ?? 0.92}
                  onChange={(v) => void patch({ semantic_cache_threshold: v })}
                  min={0.5}
                  max={1}
                  step={0.01}
                  info={threshDef.helpText}
                />
              </SettingRow>
            )}
          </>
        )
      })()}

      {/* Memory consolidation */}
      {(() => {
        const def = getDef("retrieval.pipelineStages.memoryConsolidation")
        if (!def) return null
        return (
          <SettingRow def={def}>
            <Switch
              size="sm"
              aria-label={def.label}
              checked={settings.enable_memory_consolidation ?? false}
              onCheckedChange={(v) => void patch({ enable_memory_consolidation: v })}
            />
          </SettingRow>
        )
      })()}

      {/* Context compression */}
      {(() => {
        const def = getDef("retrieval.pipelineStages.contextCompression")
        if (!def) return null
        return (
          <SettingRow def={def}>
            <Switch
              size="sm"
              aria-label={def.label}
              checked={settings.enable_context_compression ?? false}
              onCheckedChange={(v) => void patch({ enable_context_compression: v })}
            />
          </SettingRow>
        )
      })()}

      {/* Contextual chunks */}
      {(() => {
        const def = getDef("retrieval.pipelineStages.contextualChunks")
        if (!def) return null
        return (
          <SettingRow def={def}>
            <Switch
              size="sm"
              aria-label={def.label}
              checked={settings.enable_contextual_chunks ?? false}
              onCheckedChange={(v) => void patch({ enable_contextual_chunks: v })}
            />
          </SettingRow>
        )
      })()}

    </div>
  )
}
