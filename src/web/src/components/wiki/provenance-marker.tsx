// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Inline provenance marker for Wiki sections — a small icon + tooltip
// indicating the data origin and reliability signal for the section
// it precedes. Per design-system-v2 §5.2.
//
// Marker semantics:
//   auto         — synthesized by the curator agent from corpus mentions
//   user-edited  — last write was a user edit (writes the vault note)
//   contradicted — section's claims have an unresolved contradiction
//   uncertain    — confidence_band is "low" or signal is incomplete

import { Bot, Pencil, AlertTriangle, HelpCircle } from "lucide-react"

export type ProvenanceKind = "auto" | "user-edited" | "contradicted" | "uncertain"

const PROVENANCE_META: Record<
  ProvenanceKind,
  { Icon: typeof Bot; label: string; tone: string; description: string }
> = {
  auto: {
    Icon: Bot,
    label: "Auto",
    tone: "text-muted-foreground",
    description: "Synthesized by Cerid from corpus mentions.",
  },
  "user-edited": {
    Icon: Pencil,
    label: "Edited",
    tone: "text-primary",
    description: "You edited this section. Edits are persisted to the vault note.",
  },
  contradicted: {
    Icon: AlertTriangle,
    label: "Contradicted",
    tone: "text-destructive",
    description: "This section has unresolved contradictions in source material.",
  },
  uncertain: {
    Icon: HelpCircle,
    label: "Uncertain",
    tone: "text-amber-500",
    description: "Confidence is low — limited or conflicting evidence in the corpus.",
  },
}

export interface ProvenanceMarkerProps {
  kind: ProvenanceKind
  /** Optional override label */
  label?: string
}

export function ProvenanceMarker({ kind, label }: ProvenanceMarkerProps) {
  const meta = PROVENANCE_META[kind]
  const { Icon } = meta
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border border-border/60 bg-card/40 px-1.5 py-0.5 text-label-xxs font-medium ${meta.tone}`}
      title={meta.description}
      aria-label={`${label ?? meta.label}: ${meta.description}`}
    >
      <Icon className="h-2.5 w-2.5" aria-hidden="true" />
      <span>{label ?? meta.label}</span>
    </span>
  )
}
