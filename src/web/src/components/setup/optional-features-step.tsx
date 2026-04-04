// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Settings2, ChevronDown, ChevronRight, Check, Cpu, Globe, Zap } from "lucide-react"
import { OllamaStep } from "./ollama-step"
import { cn } from "@/lib/utils"

interface OllamaState {
  detected: boolean
  enabled: boolean
  model: string | null
  pulling: boolean
}

interface OptionalFeaturesStepProps {
  ollamaDetected: boolean
  ollamaModels: string[]
  ollamaState: OllamaState
  onOllamaChange: (state: OllamaState) => void
}

function CollapsibleSection({
  icon: Icon,
  title,
  badge,
  defaultOpen = false,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  badge?: React.ReactNode
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-lg border bg-card">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
        <Icon className="h-4 w-4 shrink-0 text-brand" />
        <span className="flex-1 text-xs font-medium">{title}</span>
        {badge}
      </button>
      {open && <div className="border-t px-3 py-3">{children}</div>}
    </div>
  )
}

export function OptionalFeaturesStep({
  ollamaDetected,
  ollamaModels,
  ollamaState,
  onOllamaChange,
}: OptionalFeaturesStepProps) {
  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Settings2 className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Optional Features</h3>
      <p className="mb-4 text-center text-xs text-muted-foreground">
        These are not required — you can enable them later in Settings.
      </p>

      <div className="space-y-2">
        {/* Local LLM (Ollama) */}
        <CollapsibleSection
          icon={Cpu}
          title="Local LLM (Ollama)"
          defaultOpen={ollamaDetected}
          badge={
            ollamaDetected ? (
              <Badge variant="outline" className="border-green-500/30 text-[9px] text-green-600 dark:text-green-400">
                <Check className="mr-0.5 h-2.5 w-2.5" />
                Detected
              </Badge>
            ) : (
              <Badge variant="outline" className="text-[9px] text-muted-foreground">
                Not running
              </Badge>
            )
          }
        >
          <OllamaStep
            ollamaDetected={ollamaDetected}
            ollamaModels={ollamaModels}
            state={ollamaState}
            onChange={onOllamaChange}
          />
        </CollapsibleSection>

        {/* External Data Sources */}
        <CollapsibleSection
          icon={Globe}
          title="External Data Sources"
          badge={
            <Badge variant="outline" className="text-[9px] text-muted-foreground">
              3 available
            </Badge>
          }
        >
          <p className="mb-3 text-[11px] text-muted-foreground">
            Enrich AI responses with external knowledge. Results are ephemeral by default.
          </p>
          <div className="space-y-2">
            {[
              { id: "duckduckgo", label: "DuckDuckGo Search", desc: "Web search fallback for topics not in your KB" },
              { id: "wikipedia", label: "Wikipedia", desc: "Encyclopedia lookups for factual context" },
              { id: "wolfram", label: "Wolfram Alpha", desc: "Math and scientific computation" },
            ].map((src) => (
              <div key={src.id} className="flex items-center justify-between rounded border px-2.5 py-2">
                <div>
                  <p className="text-xs font-medium">{src.label}</p>
                  <p className="text-[10px] text-muted-foreground">{src.desc}</p>
                </div>
                <Switch defaultChecked={src.id === "duckduckgo"} />
              </div>
            ))}
          </div>
        </CollapsibleSection>

        {/* Optional Services */}
        <CollapsibleSection
          icon={Zap}
          title="Optional Services"
          badge={
            <Badge variant="outline" className="text-[9px] text-muted-foreground">
              Auto-managed
            </Badge>
          }
        >
          <div className="flex items-center justify-between rounded border px-2.5 py-2">
            <div>
              <p className="text-xs font-medium">Bifrost (LLM Gateway)</p>
              <p className="text-[10px] text-muted-foreground">
                Runs silently as a fallback router. No configuration needed.
              </p>
            </div>
            <Badge variant="outline" className="text-[9px]">Auto</Badge>
          </div>
        </CollapsibleSection>
      </div>
    </>
  )
}
