// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pro Automations card (UX consolidation).
//
// Lifts Phase J (inbox triage) + Phase K (daily digest) out of env-only
// configuration and into Settings → System. Pro-gated; community users
// see a lock overlay.
//
// Per automation: toggle + cadence picker (preset cron expressions) +
// Run Now button. Status shows whether the feature flag is on
// server-side and the effective cron.

import { useCallback, useEffect, useState } from "react"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Loader2,
  Mail,
  Newspaper,
  Play,
  RefreshCw,
  Sparkles,
} from "lucide-react"
import { cn } from "@/lib/utils"
import {
  listProAutomations,
  resetProAutomation,
  runProAutomationNow,
  updateProAutomation,
  type AutomationState,
} from "@/lib/api/settings"

const FEATURE_ICONS: Record<string, typeof Mail> = {
  inbox_triage: Mail,
  daily_digest: Newspaper,
}

interface ProAutomationsCardProps {
  tier?: string  // "community" | "pro" — locks the editor when community
}

export function ProAutomationsCard({ tier = "community" }: ProAutomationsCardProps) {
  const [automations, setAutomations] = useState<AutomationState[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastRun, setLastRun] = useState<Record<string, string>>({})

  const isPro = tier !== "community"

  const refresh = useCallback(async () => {
    setError(null)
    try {
      const list = await listProAutomations()
      setAutomations(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load automations")
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleToggle = useCallback(async (name: string, next: boolean) => {
    setBusy(name)
    setError(null)
    try {
      const updated = await updateProAutomation(name, { enabled: next })
      setAutomations((prev) => prev.map((a) => (a.feature === name ? updated : a)))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed")
    } finally {
      setBusy(null)
    }
  }, [])

  const handleSchedule = useCallback(async (name: string, cron: string) => {
    setBusy(name)
    setError(null)
    try {
      const updated = await updateProAutomation(name, { schedule: cron })
      setAutomations((prev) => prev.map((a) => (a.feature === name ? updated : a)))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Schedule update failed")
    } finally {
      setBusy(null)
    }
  }, [])

  const handleRunNow = useCallback(async (name: string) => {
    setBusy(`${name}:run`)
    setError(null)
    try {
      const result = await runProAutomationNow(name)
      setLastRun((prev) => ({ ...prev, [name]: result.detail }))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Run failed")
    } finally {
      setBusy(null)
    }
  }, [])

  // handleReset removed — Pro-automations reset is no longer exposed in
  // the UI (operators use pkb_maintain instead). Function and resetProAutomation
  // import kept in case the reset button comes back in a settings refresh.
  void resetProAutomation

  return (
    <Card
      className={cn("p-4 space-y-3", !isPro && "relative")}
      data-testid="pro-automations-card"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-amber-500" />
            Pro Automations
            {!isPro && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-600">
                Pro
              </span>
            )}
          </h3>
          <p className="text-xs text-muted-foreground mt-1">
            Scheduled background tasks. Edits persist to Redis and override the
            <code className="mx-1 px-1 rounded bg-muted text-[10px]">CERID_*</code>
            env defaults.
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={refresh}
          aria-label="Refresh automation state"
          data-testid="pro-automations-refresh"
        >
          <RefreshCw className="w-4 h-4" />
        </Button>
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

      <div className="space-y-2">
        {automations.map((auto) => {
          const Icon = FEATURE_ICONS[auto.feature] ?? Clock
          const featureLocked = !isPro || !auto.feature_flag_enabled
          const rowBusy = busy === auto.feature || busy === `${auto.feature}:run`
          return (
            <Card
              key={auto.feature}
              className={cn(
                "p-3",
                auto.enabled && "border-green-500/30",
                !auto.feature_flag_enabled && "opacity-60",
              )}
              data-testid={`pro-automation-${auto.feature}`}
            >
              <div className="flex items-start gap-3">
                <Icon className="w-5 h-5 text-muted-foreground flex-shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0 space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{auto.display_name}</span>
                    {auto.enabled ? (
                      <span className="inline-flex items-center gap-1 text-xs text-green-600">
                        <CheckCircle2 className="w-3 h-3" /> active
                      </span>
                    ) : (
                      <span className="text-xs text-muted-foreground">paused</span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">{auto.description}</p>

                  {/* Cadence picker */}
                  <div className="flex items-center gap-2 text-xs">
                    <label className="text-muted-foreground" htmlFor={`cron-${auto.feature}`}>
                      Cadence:
                    </label>
                    <select
                      id={`cron-${auto.feature}`}
                      value={auto.schedule}
                      onChange={(e) => handleSchedule(auto.feature, e.target.value)}
                      disabled={featureLocked || rowBusy}
                      className="rounded border bg-background px-2 py-1 text-xs"
                      data-testid={`pro-automation-schedule-${auto.feature}`}
                    >
                      {/* Always render the current cron — even when it's a
                          custom expression not matching any preset — so the
                          select reflects state honestly. */}
                      {!auto.cadence_presets.some((p) => p.cron === auto.schedule) &&
                        auto.schedule && (
                          <option value={auto.schedule}>
                            Custom: {auto.schedule}
                          </option>
                        )}
                      {auto.cadence_presets.map((p) => (
                        <option key={p.cron || "off"} value={p.cron}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                    {lastRun[auto.feature] && (
                      <span className="text-muted-foreground">
                        · last run: {lastRun[auto.feature]}
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex flex-col gap-1.5 items-end">
                  {/* Toggle */}
                  <button
                    role="switch"
                    aria-checked={auto.enabled}
                    onClick={() => handleToggle(auto.feature, !auto.enabled)}
                    disabled={featureLocked || rowBusy}
                    className={cn(
                      "px-2 py-1 rounded text-xs font-medium transition-colors",
                      auto.enabled
                        ? "bg-primary/15 text-primary"
                        : "bg-muted text-muted-foreground",
                      featureLocked && "cursor-not-allowed opacity-50",
                    )}
                    data-testid={`pro-automation-toggle-${auto.feature}`}
                  >
                    {busy === auto.feature ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : auto.enabled ? "on" : "off"}
                  </button>

                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleRunNow(auto.feature)}
                    disabled={featureLocked || rowBusy}
                    data-testid={`pro-automation-run-${auto.feature}`}
                  >
                    {busy === `${auto.feature}:run` ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <>
                        <Play className="w-3 h-3 mr-1" />
                        Run now
                      </>
                    )}
                  </Button>
                </div>
              </div>

              {!auto.feature_flag_enabled && (
                <p className="text-xs text-amber-600 mt-2 pl-8">
                  Feature flag <code>{auto.feature_flag}</code> is off — enable Pro tier
                  to activate.
                </p>
              )}
            </Card>
          )
        })}
      </div>

      {!isPro && (
        <div
          className="absolute inset-0 flex items-center justify-center bg-background/60 backdrop-blur-sm rounded-md"
          data-testid="pro-automations-locked-overlay"
        >
          <div className="text-center p-4">
            <Sparkles className="w-8 h-8 mx-auto text-amber-500 mb-2" />
            <h4 className="text-sm font-semibold">Pro Automations</h4>
            <p className="text-xs text-muted-foreground mt-1 max-w-xs">
              Inbox triage + daily digest schedules. Upgrade to enable scheduled
              background AI tasks.
            </p>
          </div>
        </div>
      )}
    </Card>
  )
}
