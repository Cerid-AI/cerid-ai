// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { Shield, Lock, Eye, EyeOff, AlertTriangle } from "lucide-react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Switch } from "@/components/ui/switch"
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { SettingRow, AdvancedDisclosure, ReadOnlyEnvHint } from "@/components/settings/settings-primitives"
import { getDef } from "@/lib/settings-registry"
import { useSettings } from "@/hooks/use-settings"
import { DataEgressSection } from "@/components/settings/data-egress-section"
import {
  PRIVATE_MODE_EGRESS_NOTE,
  PRIVATE_MODE_LEVELS,
  PRIVATE_MODE_WITHHOLDS_COLUMNS,
} from "@/lib/private-mode-levels"
import type { SettingsCategoryPageProps } from "./page-props"

// ── Private Mode level metadata ───────────────────────────────────────────────
// Copy comes from the shared contract (private-mode-levels.json); only the
// visual treatment is decided here.

const LEVEL_STYLE: { colorClass: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { colorClass: "border-border bg-muted/40 text-muted-foreground", icon: Eye },
  { colorClass: "border-green-500/40 bg-green-500/10 text-green-700 dark:text-green-400", icon: EyeOff },
  { colorClass: "border-yellow-500/40 bg-yellow-500/10 text-yellow-700 dark:text-yellow-400", icon: EyeOff },
  { colorClass: "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-400", icon: Shield },
  { colorClass: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-400", icon: Shield },
]

const LEVEL_META = PRIVATE_MODE_LEVELS.map((l) => ({
  value: l.level,
  label: l.label,
  description: l.description,
  withholds: l.withholds,
  ...LEVEL_STYLE[l.level],
}))

// ── Private Mode section ──────────────────────────────────────────────────────

function PrivateModeSection() {
  const { privateModeLevel, changePrivateModeLevel } = useSettings()
  const def = getDef("privacy.mode.level")!
  const current = LEVEL_META.find((m) => m.value === privateModeLevel) ?? LEVEL_META[0]
  const CurrentIcon = current.icon

  const handleSelectLevel = (level: number) => {
    if (level === 4) return
    changePrivateModeLevel(level)
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <span className="text-label-xs uppercase text-muted-foreground tracking-wider">Private Mode</span>
      </CardHeader>
      <CardContent className="density-stack">
        <SettingRow def={def}>
          <div
            className={`flex items-center gap-2 rounded border px-2.5 py-1 text-xs font-medium ${current.colorClass}`}
          >
            <CurrentIcon className="h-3.5 w-3.5 shrink-0" />
            <span>{current.label}</span>
          </div>
        </SettingRow>
        <p className="text-label-xs text-muted-foreground">
          Scope: {def.scopeOfEffect.display}
        </p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {LEVEL_META.map((m) => {
            const Icon = m.icon
            const isActive = privateModeLevel === m.value
            if (m.value === 4) {
              return (
                <AlertDialog key={m.value}>
                  <AlertDialogTrigger asChild>
                    <button
                      type="button"
                      className={`flex flex-col gap-1 rounded-md border p-3 text-left transition-colors hover:border-foreground/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                        isActive ? m.colorClass : "border-border bg-card"
                      }`}
                      aria-pressed={isActive}
                    >
                      <div className="flex items-center gap-1.5">
                        <Icon className="h-3.5 w-3.5 shrink-0" />
                        <span className="text-xs font-semibold">{m.label}</span>
                      </div>
                      <p className="text-label-xs text-muted-foreground">{m.description}</p>
                    </button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Enable L4 — Full ephemeral?</AlertDialogTitle>
                      <AlertDialogDescription>
                        L4 withholds everything L3 does. When a tab closes at L4, the server
                        forgets that tab&apos;s private chats permanently, with anything they
                        produced, and keeps a receipt; when the last L4 tab closes, Private Mode
                        switches itself off. The scope is{" "}
                        <strong>global for this server — all tabs and sessions</strong>. It does
                        not block requests to the model provider.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={() => changePrivateModeLevel(4)}
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      >
                        Enable L4
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              )
            }
            return (
              <button
                key={m.value}
                type="button"
                onClick={() => handleSelectLevel(m.value)}
                className={`flex flex-col gap-1 rounded-md border p-3 text-left transition-colors hover:border-foreground/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  isActive ? m.colorClass : "border-border bg-card"
                }`}
                aria-pressed={isActive}
              >
                <div className="flex items-center gap-1.5">
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span className="text-xs font-semibold">{m.label}</span>
                </div>
                <p className="text-label-xs text-muted-foreground">{m.description}</p>
              </button>
            )
          })}
        </div>
        <dl className="grid gap-x-4 gap-y-1 text-label-xs sm:grid-cols-[max-content_1fr]">
          {PRIVATE_MODE_WITHHOLDS_COLUMNS.map((col) => (
            <div key={col.key} className="contents">
              <dt className="font-medium text-foreground">{col.title}</dt>
              <dd className="text-muted-foreground">{current.withholds[col.key]}</dd>
            </div>
          ))}
        </dl>
        <p className="text-label-xs text-muted-foreground">{PRIVATE_MODE_EGRESS_NOTE}</p>
        {privateModeLevel === 4 && (
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription className="text-xs">
              L4 is active. Closing this tab forgets its private chats permanently. When the last
              L4 tab closes, Private Mode switches itself off. This setting affects all tabs on
              this server.
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}

// ── Retrieval Privacy section (Task 1.2e opt-in, made toggleable — 1.3c) ──────

function RetrievalPrivacySection({ settings, patch }: Pick<SettingsCategoryPageProps, "settings" | "patch">) {
  const enabled = settings.sensitive_domain_retrieval ?? false

  return (
    <Card>
      <CardHeader className="pb-2">
        <span className="text-label-xs uppercase text-muted-foreground tracking-wider">Retrieval Privacy</span>
      </CardHeader>
      <CardContent className="density-stack">
        <div className="flex items-center justify-between gap-4">
          <div className="space-y-0.5">
            <p className="text-sm font-medium">Include private domains (iMessage) in answers</p>
            <p className="text-label-xs text-muted-foreground">
              Off by default. Independent of Private Mode — enabling this lets retrieval surface
              content from sensitive domains, such as iMessage, when answering.
            </p>
          </div>
          <Switch
            aria-label="Include private domains (iMessage) in answers"
            checked={enabled}
            onCheckedChange={(next) => void patch({ sensitive_domain_retrieval: next })}
          />
        </div>
      </CardContent>
    </Card>
  )
}

// ── Data protection section ───────────────────────────────────────────────────

function DataProtectionSection() {
  const encDef = getDef("privacy.data.encryption")!
  const anonDef = getDef("privacy.data.anonymizeEmailHeaders")!
  const auditDef = getDef("privacy.data.auditRetentionDays")!

  return (
    <Card>
      <CardHeader className="pb-2">
        <span className="text-label-xs uppercase text-muted-foreground tracking-wider">Data Protection</span>
      </CardHeader>
      <CardContent className="density-stack">
        <SettingRow def={encDef}>
          <div className="flex items-center gap-2">
            <Lock className="h-4 w-4 text-muted-foreground" />
            <ReadOnlyEnvHint envVar="CERID_ENCRYPTION" />
          </div>
        </SettingRow>
        <AdvancedDisclosure category="privacy" group="data">
          <SettingRow def={anonDef}>
            <ReadOnlyEnvHint envVar="CERID_ANONYMIZE_EMAIL_HEADERS" />
          </SettingRow>
          <SettingRow def={auditDef}>
            <ReadOnlyEnvHint envVar="AUDIT_RETENTION_DAYS" />
          </SettingRow>
        </AdvancedDisclosure>
      </CardContent>
    </Card>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PrivacyCategory({ settings, patch }: SettingsCategoryPageProps) {
  return (
    <div className="density-stack">
      <PrivateModeSection />
      <RetrievalPrivacySection settings={settings} patch={patch} />
      <DataEgressSection />
      <DataProtectionSection />
    </div>
  )
}
