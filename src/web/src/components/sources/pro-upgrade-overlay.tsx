// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F8 — Pro upgrade overlay.
 *
 * Liquid Glass dialog shown when a community user clicks a Pro-gated
 * connector tile. Shows the connector's name, three-bullet value
 * proposition, Upgrade button (deep-links to checkout), and a
 * Continue-with-free option that returns to the gallery.
 *
 * Continuity-of-intent promise: after upgrade success, the caller
 * should re-open the F3 wizard pre-filled with this kind.
 */

import { Check, Lock } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { descriptorFor } from "./source-kind-icons"

const BULLETS = [
  "Cloud connectors (Gmail, Outlook, Google + Microsoft Calendars)",
  "Meeting Capture — transcription with calendar-aware stitching",
  "Custom Smart RAG — per-source weighting + retrieval orchestration",
]

/** Where a self-hosted user goes to compare plans and buy. */
const PRICING_URL = "https://cerid.ai/pricing"

interface ProUpgradeOverlayProps {
  open: boolean
  kind: string | null
  onClose: () => void
  /** Overrides the default upgrade action (open the pricing page). Hosts that
   *  can start checkout in-app pass their own. Defaulted rather than optional-
   *  with-no-fallback: an Upgrade button that silently does nothing is worse
   *  than no button. */
  onUpgrade?: (kind: string) => void
}

export function ProUpgradeOverlay({
  open,
  kind,
  onClose,
  onUpgrade,
}: ProUpgradeOverlayProps) {
  const desc = kind ? descriptorFor(kind) : null
  const Icon = desc?.icon

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="liquid-glass max-w-md border-none">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Lock className="h-4 w-4 text-amber-500" />
            {desc ? `${desc.label} requires Cerid Pro` : "Cerid Pro"}
          </DialogTitle>
          <DialogDescription>
            Continue your knowledge graph beyond local sources.
          </DialogDescription>
        </DialogHeader>

        {desc && Icon && (
          <div className="flex items-center gap-3 rounded-md border border-border bg-card/40 px-4 py-3">
            <Icon className="h-6 w-6 text-foreground/80" />
            <div>
              <div className="text-sm font-medium text-foreground">
                {desc.label}
              </div>
              <div className="text-label-xs text-muted-foreground">
                {desc.blurb}
              </div>
            </div>
          </div>
        )}

        <ul className="space-y-2 py-2">
          {BULLETS.map((b) => (
            <li key={b} className="flex items-start gap-2 text-sm text-foreground">
              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
              <span>{b}</span>
            </li>
          ))}
        </ul>

        <p className="text-label-xs text-muted-foreground">
          14-day free trial, no credit card — start it in Settings → Plan &amp; Billing.
        </p>

        <div className="flex items-center justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} className="cerid-press">
            Continue with free
          </Button>
          <Button
            onClick={() => {
              if (onUpgrade) {
                if (kind) onUpgrade(kind)
                return
              }
              window.open(PRICING_URL, "_blank", "noopener,noreferrer")
              onClose()
            }}
            className="cerid-press"
          >
            See Pro plans
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
