// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import type { ServerSettings, SettingsUpdate } from "@/lib/types"
import type { SectionKey } from "./settings-primitives"
import { cn } from "@/lib/utils"
import { PRESETS, detectActivePreset } from "@/lib/settings-presets"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"
import { ChevronDown, ChevronRight, Cpu, SearchIcon, Layers, Crown } from "lucide-react"
import { SectionHeading, Row, SliderRow, PipelineToggle, ProGate } from "./settings-primitives"

interface PipelineSectionProps {
  settings: ServerSettings
  sections: Record<SectionKey, boolean>
  toggleSection: (key: SectionKey) => void
  patch: (update: SettingsUpdate) => Promise<void>
}

export function PipelineSection({ settings, sections, toggleSection, patch }: PipelineSectionProps) {
  const [pipelineCustomize, setPipelineCustomize] = useState(false)

  return (
    <>
      {/* -- Retrieval Pipeline -- */}
      <SectionHeading icon={Cpu} label="Retrieval Pipeline" open={sections.retrieval} onToggle={() => toggleSection("retrieval")} />
      {sections.retrieval && (
        <Card className="mb-4">
          <CardHeader className="px-4 pb-2 pt-4">
            <CardDescription className="text-xs">
              Choose a preset or customize individual pipeline stages.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 px-4 pb-4">
            {/* -- Preset cards -- */}
            {(() => {
              const activePreset = detectActivePreset(settings as unknown as Record<string, unknown>)
              const tier = settings.feature_tier ?? "community"
              return (
                <div className="grid grid-cols-3 gap-2">
                  {Object.entries(PRESETS).map(([key, preset]) => {
                    const locked = key === "maximum" && tier === "community"
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => !locked && patch(preset.values)}
                        disabled={locked}
                        className={cn(
                          "rounded-lg border p-2.5 text-left transition-colors",
                          locked
                            ? "opacity-50 cursor-not-allowed border-muted"
                            : activePreset === key
                              ? "border-primary bg-primary/5"
                              : "border-muted hover:border-muted-foreground/30",
                        )}
                      >
                        <span className="flex items-center gap-1 text-sm font-medium">
                          {preset.label}
                          {locked && (
                            <Badge variant="outline" className="text-[10px] px-1 py-0 text-teal-500 border-teal-500/30">
                              <Crown className="mr-0.5 h-2.5 w-2.5" />Pro
                            </Badge>
                          )}
                        </span>
                        <p className="mt-0.5 text-[11px] leading-tight text-muted-foreground">
                          {preset.description}
                        </p>
                      </button>
                    )
                  })}
                </div>
              )
            })()}

            {!detectActivePreset(settings as unknown as Record<string, unknown>) && (
              <p className="text-[11px] text-muted-foreground">
                Custom configuration — doesn&apos;t match any preset
              </p>
            )}

            {/* -- Customize disclosure -- */}
            <button
              type="button"
              className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
              onClick={() => setPipelineCustomize(!pipelineCustomize)}
            >
              {pipelineCustomize ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              Customize
            </button>

            {pipelineCustomize && (
              <div className="space-y-4 border-t pt-4">
                {/* Adaptive Retrieval */}
                <PipelineToggle
                  label="Adaptive Retrieval"
                  enabled={settings.enable_adaptive_retrieval ?? false}
                  onToggle={(v) => patch({ enable_adaptive_retrieval: v })}
                  description="Classifies query complexity to skip or reduce retrieval for simple queries"
                >
                  <SliderRow
                    label="Light Top-K"
                    value={settings.adaptive_retrieval_light_top_k ?? 3}
                    onChange={(v) => patch({ adaptive_retrieval_light_top_k: Math.round(v) })}
                    min={1} max={10} step={1}
                    info="Number of results for light retrieval mode"
                  />
                </PipelineToggle>

                <div className="h-px bg-border" />

                {/* Query Decomposition */}
                <PipelineToggle
                  label="Query Decomposition"
                  enabled={settings.enable_query_decomposition ?? false}
                  onToggle={(v) => patch({ enable_query_decomposition: v })}
                  description="Breaks complex multi-part questions into parallel sub-queries for broader coverage"
                >
                  <SliderRow
                    label="Max Sub-queries"
                    value={settings.query_decomposition_max_subqueries ?? 4}
                    onChange={(v) => patch({ query_decomposition_max_subqueries: Math.round(v) })}
                    min={2} max={6} step={1}
                    info="Maximum number of sub-queries per decomposition"
                  />
                </PipelineToggle>

                <div className="h-px bg-border" />

                {/* MMR Diversity */}
                <PipelineToggle
                  label="MMR Diversity"
                  enabled={settings.enable_mmr_diversity ?? false}
                  onToggle={(v) => patch({ enable_mmr_diversity: v })}
                  description="Reorders results using Maximal Marginal Relevance for diverse, non-redundant context"
                >
                  <SliderRow
                    label="Lambda"
                    value={settings.mmr_lambda ?? 0.7}
                    onChange={(v) => patch({ mmr_lambda: v })}
                    min={0} max={1} step={0.05}
                    info="Balance between relevance (1.0) and diversity (0.0)"
                  />
                </PipelineToggle>

                <div className="h-px bg-border" />

                {/* Intelligent Assembly */}
                <PipelineToggle
                  label="Intelligent Assembly"
                  enabled={settings.enable_intelligent_assembly ?? false}
                  onToggle={(v) => patch({ enable_intelligent_assembly: v })}
                  description="Three-pass context assembly maximizing query facet coverage"
                />

                <div className="h-px bg-border" />

                {/* Late Interaction */}
                <PipelineToggle
                  label="Late Interaction"
                  enabled={settings.enable_late_interaction ?? false}
                  onToggle={(v) => patch({ enable_late_interaction: v })}
                  description="ColBERT-inspired MaxSim scoring for fine-grained token-level relevance"
                >
                  <SliderRow
                    label="Top-N Candidates"
                    value={settings.late_interaction_top_n ?? 8}
                    onChange={(v) => patch({ late_interaction_top_n: Math.round(v) })}
                    min={4} max={16} step={1}
                    info="Number of candidates for late interaction scoring"
                  />
                  <SliderRow
                    label="Blend Weight"
                    value={settings.late_interaction_blend_weight ?? 0.15}
                    onChange={(v) => patch({ late_interaction_blend_weight: v })}
                    min={0} max={0.5} step={0.05}
                    info="Weight of late interaction score blended into final ranking"
                  />
                </PipelineToggle>

                <div className="h-px bg-border" />

                {/* Semantic Cache */}
                <PipelineToggle
                  label="Semantic Cache"
                  enabled={settings.enable_semantic_cache ?? false}
                  onToggle={(v) => patch({ enable_semantic_cache: v })}
                  description="Caches retrieval results keyed by semantic query similarity"
                >
                  <SliderRow
                    label="Similarity Threshold"
                    value={settings.semantic_cache_threshold ?? 0.92}
                    onChange={(v) => patch({ semantic_cache_threshold: v })}
                    min={0.8} max={1} step={0.01}
                    info="Minimum cosine similarity for a cache hit"
                  />
                </PipelineToggle>

                <div className="h-px bg-border" />

                {/* Memory Consolidation */}
                <PipelineToggle
                  label="Memory Consolidation"
                  enabled={settings.enable_memory_consolidation ?? true}
                  onToggle={(v) => patch({ enable_memory_consolidation: v })}
                  description="Deduplicate and merge similar memories during extraction"
                />

                <div className="h-px bg-border" />

                {/* Context Compression */}
                <PipelineToggle
                  label="Context Compression"
                  enabled={settings.enable_context_compression ?? true}
                  onToggle={(v) => patch({ enable_context_compression: v })}
                  description="Compress conversation history to fit within model context window"
                />
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* -- Search Tuning -- */}
      <SectionHeading icon={SearchIcon} label="Search Tuning" open={sections.search} onToggle={() => toggleSection("search")} />
      {sections.search && (
        <Card className="mb-4">
          <CardContent className="grid gap-4 pt-4">
            <SliderRow
              label="Vector Weight"
              value={settings.hybrid_vector_weight ?? 0.6}
              onChange={(v) => patch({ hybrid_vector_weight: v })}
              min={0} max={1} step={0.05}
              info="Weight for vector similarity in hybrid search (0-1)"
            />
            <SliderRow
              label="Keyword Weight"
              value={settings.hybrid_keyword_weight ?? 0.4}
              onChange={(v) => patch({ hybrid_keyword_weight: v })}
              min={0} max={1} step={0.05}
              info="Weight for BM25 keyword matching in hybrid search (0-1)"
            />

            <div className="my-1 h-px bg-border" />

            <SliderRow
              label="Rerank LLM Weight"
              value={settings.rerank_llm_weight ?? 0.6}
              onChange={(v) => patch({ rerank_llm_weight: v })}
              min={0} max={1} step={0.05}
              info="Weight for LLM-based reranking score (0-1)"
            />
            <SliderRow
              label="Rerank Original Weight"
              value={settings.rerank_original_weight ?? 0.4}
              onChange={(v) => patch({ rerank_original_weight: v })}
              min={0} max={1} step={0.05}
              info="Weight for original relevance score in reranking (0-1)"
            />

            <div className="my-1 h-px bg-border" />

            <Row
              label="Temporal Half-life"
              value={settings.temporal_half_life_days ? `${settings.temporal_half_life_days} days` : "\u2014"}
              info="Days until temporal recency boost decays by half"
            />
            <Row
              label="Recency Weight"
              value={(settings.temporal_recency_weight ?? 0.1).toFixed(2)}
              info="Maximum boost from document recency"
            />
          </CardContent>
        </Card>
      )}

      {/* -- Custom Smart RAG Config (Pro tier) -- */}
      <SectionHeading icon={Layers} label="Smart RAG Config" open={sections.rag_config} onToggle={() => toggleSection("rag_config")} />
      {sections.rag_config && (
        <Card className="mb-4">
          <CardHeader className="px-4 pb-2 pt-4">
            <CardDescription className="flex items-center gap-2 text-xs">
              Per-source weights and toggles for Custom Smart RAG mode.
              {(settings.feature_tier ?? "community") === "community" && (
                <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-teal-500">Pro</Badge>
              )}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 px-4 pb-4">
            <ProGate tier={settings.feature_tier ?? "community"}>
              <div className="grid gap-4">
                <SliderRow
                  label="KB Source Weight"
                  value={1.0}
                  onChange={() => {}}
                  min={0} max={2} step={0.1}
                  info="Weight multiplier for KB relevance scores"
                />
                <SliderRow
                  label="Memory Weight"
                  value={1.0}
                  onChange={() => {}}
                  min={0} max={2} step={0.1}
                  info="Weight multiplier for memory recall scores"
                />
                <SliderRow
                  label="External Weight"
                  value={1.0}
                  onChange={() => {}}
                  min={0} max={2} step={0.1}
                  info="Weight multiplier for external source scores"
                />

                <div className="h-px bg-border" />

                <div className="space-y-2">
                  <p className="text-xs font-medium">Memory Type Filters</p>
                  <p className="text-[11px] text-muted-foreground">
                    Select which memory types are included in Smart/Custom Smart recall.
                  </p>
                  {["empirical", "decision", "preference", "project_context", "temporal", "conversational"].map((type) => (
                    <label key={type} className="flex items-center gap-2 text-xs">
                      <input type="checkbox" defaultChecked className="rounded border-muted-foreground/30" />
                      {type.replace("_", " ")}
                    </label>
                  ))}
                </div>
              </div>
            </ProGate>
          </CardContent>
        </Card>
      )}
    </>
  )
}
