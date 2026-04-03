// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Badge } from "@/components/ui/badge"
import {
  Crown,
  Check,
  X,
} from "lucide-react"

interface ProSectionProps {
  featureTier: string
  featureFlags: Record<string, boolean>
  onRefresh?: () => void
}

const PRO_FEATURES = [
  { key: "audio_transcription", label: "Audio Transcription", description: "Transcribe meeting notes and audio files via Whisper" },
  { key: "image_understanding", label: "Vision Analysis", description: "Analyze images and diagrams using LLM vision" },
  { key: "metamorphic_verification", label: "Metamorphic Verification", description: "Advanced claim verification with rephrased queries" },
  { key: "custom_smart_rag", label: "Custom Smart RAG", description: "Per-source weight tuning for retrieval orchestration" },
  { key: "advanced_analytics", label: "Advanced Analytics", description: "Extended observability metrics and quality scoring" },
] as const

const COMMUNITY_FEATURES = [
  { key: "ocr_parsing", label: "OCR Text Extraction", description: "Extract text from scanned PDFs and images" },
  { key: "semantic_dedup", label: "Semantic Deduplication", description: "Detect near-duplicate content using embeddings" },
] as const

const ENTERPRISE_FEATURES = [
  { key: "multi_user", label: "Multi-User Auth", description: "JWT-based multi-user with per-user API keys" },
  { key: "sso_saml", label: "SSO / SAML", description: "SAML 2.0 SP with IdP metadata import" },
  { key: "audit_logging", label: "Audit Logging", description: "Comprehensive audit trail for compliance" },
  { key: "priority_support", label: "Priority Support", description: "SLA-backed support and incident response" },
] as const

export function ProSection({ featureTier, featureFlags }: Omit<ProSectionProps, "onRefresh">) {
  const isPro = featureTier === "pro" || featureTier === "enterprise"

  return (
    <div className="space-y-6">
      {/* Current Plan */}
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2">
          <Crown className={isPro ? "h-5 w-5 text-amber-500" : "h-5 w-5 text-muted-foreground"} />
          <h3 className="text-sm font-semibold">
            Current Plan:{" "}
            {featureTier === "enterprise" ? "Vault" : featureTier === "pro" ? "Pro" : "Core"}
          </h3>
        </div>
        {!isPro && (
          <p className="mt-2 text-xs text-muted-foreground">
            Pro features available separately. Visit cerid.ai for details.
          </p>
        )}
      </div>

      {/* Pro Features */}
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400">Pro</Badge>
          Features
        </h3>
        <div className="space-y-1">
          {PRO_FEATURES.map((f) => (
            <div key={f.key} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{f.label}</div>
                <div className="text-xs text-muted-foreground truncate">{f.description}</div>
              </div>
              <div className="ml-3 shrink-0">
                {featureFlags[f.key] ? (
                  <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">
                    <Check className="mr-1 h-3 w-3" />Enabled
                  </Badge>
                ) : (
                  <Badge variant="outline" className="border-muted-foreground/30 text-muted-foreground">
                    <X className="mr-1 h-3 w-3" />Locked
                  </Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Community Features (always enabled, shown for completeness) */}
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Badge variant="outline" className="border-zinc-500/30 bg-zinc-500/10 text-zinc-600 dark:text-zinc-400">Core</Badge>
          Included in All Plans
        </h3>
        <div className="space-y-1">
          {COMMUNITY_FEATURES.map((f) => (
            <div key={f.key} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{f.label}</div>
                <div className="text-xs text-muted-foreground truncate">{f.description}</div>
              </div>
              <div className="ml-3 shrink-0">
                <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">
                  <Check className="mr-1 h-3 w-3" />Enabled
                </Badge>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Enterprise Features */}
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Badge variant="outline" className="border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400">Vault</Badge>
          Enterprise Features
        </h3>
        <div className="space-y-1">
          {ENTERPRISE_FEATURES.map((f) => (
            <div key={f.key} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{f.label}</div>
                <div className="text-xs text-muted-foreground truncate">{f.description}</div>
              </div>
              <div className="ml-3 shrink-0">
                {featureFlags[f.key] ? (
                  <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">
                    <Check className="mr-1 h-3 w-3" />Enabled
                  </Badge>
                ) : (
                  <Badge variant="outline" className="border-muted-foreground/30 text-muted-foreground">
                    <X className="mr-1 h-3 w-3" />Locked
                  </Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
