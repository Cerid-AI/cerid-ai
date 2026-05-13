// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { ShieldCheck, Check, X } from "lucide-react"

export interface TelemetryConsent {
  /** Sentry-style anonymous error reporting + performance metrics. */
  sendPerformance: boolean
  /** Submit numbers to the public benchmark dashboard at bench.quenchforge.dev. */
  sendBenchmark: boolean
}

interface TelemetryConsentStepProps {
  consent: TelemetryConsent
  onChange: (consent: TelemetryConsent) => void
}

/**
 * Step 3 — Telemetry Consent.
 *
 * Both toggles default OFF. Copy enumerates exactly what is and isn't sent
 * (audit-finalized in the plan). Users can change these later in
 * Settings → Observability.
 */
export function TelemetryConsentStep({
  consent,
  onChange,
}: TelemetryConsentStepProps) {
  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <ShieldCheck className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">
        Help improve Cerid AI
      </h3>
      <p className="mb-4 text-center text-xs text-muted-foreground">
        Share anonymous performance data so we can tune defaults for hardware
        like yours.
      </p>

      <div className="space-y-3">
        <div className="rounded-lg border bg-card p-3 space-y-2">
          <p className="text-label-xs font-medium text-muted-foreground">
            Sent (only if you opt in)
          </p>
          <ul className="space-y-1 text-xs text-foreground">
            <ListItem ok>Hardware profile (CPU, GPU model, RAM, OS version)</ListItem>
            <ListItem ok>Active backend (ollama, quenchforge, or cloud)</ListItem>
            <ListItem ok>
              Tokens/second and first-token latency (no prompts, no responses)
            </ListItem>
            <ListItem ok>Semantic cache hit rate</ListItem>
          </ul>
        </div>

        <div className="rounded-lg border bg-card p-3 space-y-2">
          <p className="text-label-xs font-medium text-muted-foreground">
            Never sent
          </p>
          <ul className="space-y-1 text-xs text-foreground">
            <ListItem>Your prompts or any model outputs</ListItem>
            <ListItem>File paths or document content</ListItem>
            <ListItem>IP address, account info, or anything user-identifiable</ListItem>
          </ul>
        </div>

        <div className="space-y-2 pt-1">
          <ConsentToggle
            id="telemetry-performance"
            label="Send anonymous performance data"
            description="Helps us prioritize hardware profiles and surface regressions."
            checked={consent.sendPerformance}
            onChange={(v) => onChange({ ...consent, sendPerformance: v })}
          />
          <ConsentToggle
            id="telemetry-benchmark"
            label="Send anonymous benchmark to the public dashboard"
            description="Numbers shown at bench.quenchforge.dev. No attribution."
            checked={consent.sendBenchmark}
            onChange={(v) => onChange({ ...consent, sendBenchmark: v })}
          />
        </div>

        <p className="text-center text-label-xs text-muted-foreground/80">
          You can change this any time in Settings &rarr; Observability.
        </p>
      </div>
    </>
  )
}

function ConsentToggle({
  id,
  label,
  description,
  checked,
  onChange,
}: {
  id: string
  label: string
  description: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border bg-card px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <Label htmlFor={id} className="cursor-pointer text-sm font-medium">
          {label}
        </Label>
        <p className="mt-0.5 text-label-xs text-muted-foreground">{description}</p>
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  )
}

function ListItem({ ok, children }: { ok?: boolean; children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2">
      {ok ? (
        <Check className="mt-0.5 h-3 w-3 shrink-0 text-green-600 dark:text-green-400" />
      ) : (
        <X className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
      )}
      <span>{children}</span>
    </li>
  )
}
