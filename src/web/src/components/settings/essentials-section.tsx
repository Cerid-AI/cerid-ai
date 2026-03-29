// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ServerSettings, SettingsUpdate, RoutingMode } from "@/lib/types"
import type { SectionKey } from "./settings-primitives"
import { useSettings } from "@/hooks/use-settings"
import { Card, CardContent } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Database, ToggleLeft } from "lucide-react"
import { SectionHeading, LabelWithInfo, Row, ToggleRow, SliderRow } from "./settings-primitives"

interface EssentialsSectionProps {
  settings: ServerSettings
  sections: Record<SectionKey, boolean>
  toggleSection: (key: SectionKey) => void
  patch: (update: SettingsUpdate) => Promise<void>
}

export function EssentialsSection({ settings, sections, toggleSection, patch }: EssentialsSectionProps) {
  const { routingMode, setRoutingMode } = useSettings()

  return (
    <>
      {/* -- Knowledge & Ingestion -- */}
      <SectionHeading icon={Database} label="Knowledge & Ingestion" open={sections.knowledge_ingestion} onToggle={() => toggleSection("knowledge_ingestion")} />
      {sections.knowledge_ingestion && (
        <Card className="mb-4">
          <CardContent className="grid gap-4 pt-4">
            <ToggleRow
              label="Auto-inject KB Context"
              enabled={settings.enable_auto_inject}
              onToggle={(v) => patch({ enable_auto_inject: v })}
              info="Automatically includes relevant KB context when relevance exceeds threshold"
            />
            {settings.enable_auto_inject && (
              <SliderRow
                label="Injection Threshold"
                value={settings.auto_inject_threshold}
                onChange={(v) => patch({ auto_inject_threshold: v })}
                min={0.5} max={1} step={0.05}
                info="Minimum relevance score to auto-inject (higher = more selective)"
              />
            )}

            <div className="flex items-center justify-between">
              <LabelWithInfo label="RAG Mode" info="How the system decides when to inject KB context into queries" />
              <Select
                value={settings.rag_mode ?? "smart"}
                onValueChange={(v) => patch({ rag_mode: v })}
              >
                <SelectTrigger size="sm" className="w-36">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="smart">Smart (auto-detect)</SelectItem>
                  <SelectItem value="always">Always inject KB</SelectItem>
                  <SelectItem value="manual">Manual only</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="my-1 h-px bg-border" />

            <div className="flex items-center justify-between">
              <LabelWithInfo label="Categorization Mode" info="How uploaded documents are categorized into KB domains" />
              <Select
                value={settings.categorize_mode}
                onValueChange={(v) => patch({ categorize_mode: v })}
              >
                <SelectTrigger size="sm" className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="manual">Manual</SelectItem>
                  <SelectItem value="smart">Smart</SelectItem>
                  <SelectItem value="pro">Pro</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Row
              label="Chunk Size"
              value={`${settings.chunk_max_tokens} tokens / ${settings.chunk_overlap} overlap`}
              info="Max tokens per chunk and overlap between chunks for embedding"
            />
            <div className="flex items-center justify-between">
              <LabelWithInfo label="Storage Mode" info="Extract-only parses text and discards the file. Archive keeps a copy in the sync directory." />
              <Select
                value={settings.storage_mode}
                onValueChange={(v) => patch({ storage_mode: v })}
              >
                <SelectTrigger size="sm" className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="extract_only">Extract Only</SelectItem>
                  <SelectItem value="archive">Archive</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <ToggleRow
              label="Contextual Chunking"
              enabled={settings.enable_contextual_chunks ?? false}
              onToggle={(v) => patch({ enable_contextual_chunks: v })}
              info="Adds LLM-generated situational summaries to each chunk for richer retrieval context"
            />
          </CardContent>
        </Card>
      )}

      {/* -- AI Features -- */}
      <SectionHeading icon={ToggleLeft} label="AI Features" open={sections.features} onToggle={() => toggleSection("features")} />
      {sections.features && (
        <Card className="mb-4">
          <CardContent className="grid gap-3 pt-4">
            <ToggleRow
              label="Self-RAG Validation"
              enabled={settings.enable_self_rag ?? true}
              onToggle={(v) => patch({ enable_self_rag: v })}
              info="Validates retrieval quality and retries with refined queries if results are insufficient"
            />
            <ToggleRow
              label="Feedback Loop"
              enabled={settings.enable_feedback_loop}
              onToggle={(v) => patch({ enable_feedback_loop: v })}
              info="Saves AI responses back to your knowledge base for continuous improvement"
            />
            <ToggleRow
              label="Hallucination Check"
              enabled={settings.enable_hallucination_check}
              onToggle={(v) => patch({ enable_hallucination_check: v })}
              info="Verifies factual claims in AI responses against your knowledge base"
            />
            <ToggleRow
              label="Memory Extraction"
              enabled={settings.enable_memory_extraction}
              onToggle={(v) => patch({ enable_memory_extraction: v })}
              info="Extracts key facts and preferences from conversations into long-term memory"
            />

            <div className="my-1 h-px bg-border" />

            <SliderRow
              label="Hallucination Threshold"
              value={settings.hallucination_threshold}
              onChange={(v) => patch({ hallucination_threshold: v })}
              min={0} max={1} step={0.05}
              info="Confidence threshold for flagging claims (lower = more sensitive)"
            />

            <div className="my-1 h-px bg-border" />

            <div className="flex items-center justify-between">
              <LabelWithInfo label="Model Router" info="Manual: no suggestions. Recommend: shows switch banner. Auto: silently picks the best model." />
              <Select
                value={routingMode}
                onValueChange={(v) => setRoutingMode(v as RoutingMode)}
              >
                <SelectTrigger size="sm" className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="manual">Manual</SelectItem>
                  <SelectItem value="recommend">Recommend</SelectItem>
                  <SelectItem value="auto">Auto</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center justify-between">
              <LabelWithInfo label="Cost Sensitivity" info="How aggressively the model router optimizes for cost vs quality" />
              <Select
                value={settings.cost_sensitivity ?? "medium"}
                onValueChange={(v) => patch({ cost_sensitivity: v })}
              >
                <SelectTrigger size="sm" className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>
      )}
    </>
  )
}
