// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Single row in the External APIs settings section.
 *
 * Displays adapter name + slug, a status chip, a health-check button,
 * and an enable/disable toggle.  The toggle is disabled when the adapter
 * requires an API key that hasn't been configured yet.
 */

import { useState } from "react"
import { Activity, KeyRound } from "lucide-react"
import { Switch } from "@/components/ui/switch"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { cn } from "@/lib/utils"
import type { ExternalAPISummary, ExternalAPIHealth } from "@/lib/types/external-apis"
import { fetchExternalAPIHealth } from "@/lib/api/external-apis"

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusChip({ adapter }: { adapter: ExternalAPISummary }) {
  const needsKey = adapter.requires_key && !adapter.key_configured

  if (needsKey) {
    return (
      <Badge
        variant="outline"
        className="border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400 text-[10px] px-2 py-0"
      >
        Needs key
      </Badge>
    )
  }

  return adapter.enabled ? (
    <Badge
      variant="outline"
      className="border-green-500/40 bg-green-500/10 text-green-600 dark:text-green-400 text-[10px] px-2 py-0"
    >
      Enabled
    </Badge>
  ) : (
    <Badge
      variant="outline"
      className="border-muted text-muted-foreground text-[10px] px-2 py-0"
    >
      Disabled
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ExternalAPIRowProps {
  adapter: ExternalAPISummary
  /** Called when the user flips the toggle. */
  onToggle: (slug: string, enabled: boolean) => void
  /** Whether a toggle mutation is in flight (disables controls). */
  togglePending?: boolean
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ExternalAPIRow({ adapter, onToggle, togglePending = false }: ExternalAPIRowProps) {
  const [health, setHealth] = useState<ExternalAPIHealth | null>(null)
  const [healthLoading, setHealthLoading] = useState(false)

  const needsKey = adapter.requires_key && !adapter.key_configured
  const toggleDisabled = needsKey || togglePending

  const handleHealthCheck = async () => {
    setHealthLoading(true)
    setHealth(null)
    try {
      const result = await fetchExternalAPIHealth(adapter.slug)
      setHealth(result)
    } finally {
      setHealthLoading(false)
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-3">
        {/* Name + slug */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">{adapter.display_name}</span>
            <span className="text-[10px] font-mono text-muted-foreground">{adapter.slug}</span>
            <StatusChip adapter={adapter} />
            {adapter.key_configured && adapter.requires_key && (
              <span
                className="inline-flex items-center gap-1 text-[10px] text-muted-foreground"
                aria-label="API key configured"
              >
                <KeyRound className="h-2.5 w-2.5" aria-hidden="true" />
                Key set
              </span>
            )}
          </div>
        </div>

        {/* Health check button */}
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 px-2 text-[11px]"
          onClick={handleHealthCheck}
          disabled={healthLoading}
          aria-label={`Check health for ${adapter.display_name}`}
        >
          <Activity
            className={cn("h-3.5 w-3.5", healthLoading && "animate-pulse")}
            aria-hidden="true"
          />
          {healthLoading ? "Checking…" : "Health"}
        </Button>

        {/* Enable/disable toggle */}
        <Switch
          checked={adapter.enabled}
          onCheckedChange={(v) => onToggle(adapter.slug, v)}
          disabled={toggleDisabled}
          aria-label={`${adapter.enabled ? "Disable" : "Enable"} ${adapter.display_name}`}
          title={
            needsKey
              ? `${adapter.display_name} requires an API key — set the key env var and restart`
              : undefined
          }
        />
      </div>

      {/* Inline health result */}
      {health && (
        <Alert
          variant={health.status === "ok" ? "default" : "destructive"}
          className="py-2 text-xs"
          role="status"
          aria-live="polite"
          aria-label={`Health check result for ${adapter.display_name}: ${health.status}`}
        >
          <AlertDescription className="text-xs">
            {health.status === "ok" ? (
              <span className="text-green-600 dark:text-green-400">Healthy</span>
            ) : (
              <span>
                Unreachable
                {health.detail ? ` — ${health.detail}` : ""}
              </span>
            )}
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
