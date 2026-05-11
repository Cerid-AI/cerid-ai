// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * External APIs settings section (Phase API.1 + API.2).
 *
 * Displays all 8 curated public-API adapters with enable/disable toggles
 * and per-adapter health checks.  Placed inside the Governance tab because
 * these are external-surface controls: they govern which third-party services
 * Cerid may call during agent tool invocations and wiki enrichment.
 * Governance is the correct home — not System (which is infrastructure) —
 * because toggling an adapter changes the *scope* of outbound data access,
 * a policy decision rather than a runtime one.
 *
 * Gate: <AdvancedMode> — only operators running in advanced mode see this.
 * Simple-mode users are unaffected by the defaults (all keyless adapters
 * enabled) and don't need the controls.
 */

import { Globe, Loader2, AlertCircle, RefreshCw } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { SectionHeading } from "./settings-primitives"
import { ExternalAPIRow } from "./external-api-row"
import { useExternalAPIs, useExternalAPIToggle } from "@/hooks/use-external-apis"

// ---------------------------------------------------------------------------
// Section
// ---------------------------------------------------------------------------

/**
 * External APIs section — rendered inside the Governance tab under
 * `<AdvancedMode>`.
 *
 * @param open        Whether the section is expanded.
 * @param onToggle    Called when the heading chevron is clicked.
 */
export function ExternalAPIsSection({
  open,
  onToggle,
}: {
  open: boolean
  onToggle: () => void
}) {
  const { data, isLoading, isError, error } = useExternalAPIs()
  const { mutate: toggle, isPending: togglePending } = useExternalAPIToggle()

  return (
    <>
      <SectionHeading
        icon={Globe}
        label="External APIs"
        open={open}
        onToggle={onToggle}
      />

      {open && (
        <Card className="mb-4">
          <CardContent className="pt-4 space-y-1">
            {/* Description */}
            <p className="text-label-sm text-muted-foreground leading-snug mb-3">
              Curated public APIs used for wiki enrichment and agent tool calls.
              Cerid never proxies through its own keys — keyless adapters call
              upstream services directly; key-required adapters use the
              operator-supplied env var.
            </p>

            {/* 4-state matrix */}
            {isLoading && (
              <div
                className="flex items-center gap-2 py-6 justify-center text-muted-foreground text-xs"
                role="status"
                aria-label="Loading external APIs"
              >
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Loading adapters…
              </div>
            )}

            {isError && (
              <div
                className="flex flex-col items-center gap-3 py-6 text-muted-foreground"
                role="alert"
              >
                <AlertCircle className="h-6 w-6 text-destructive" aria-hidden="true" />
                <p className="text-xs text-destructive">
                  {error?.message ?? "Failed to load external APIs"}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => window.location.reload()}
                  className="h-7 text-xs"
                >
                  <RefreshCw className="mr-1.5 h-3 w-3" aria-hidden="true" />
                  Retry
                </Button>
              </div>
            )}

            {!isLoading && !isError && data && data.length === 0 && (
              <p className="py-4 text-center text-xs text-muted-foreground">
                No adapters registered.
              </p>
            )}

            {!isLoading && !isError && data && data.length > 0 && (
              <div className="divide-y divide-border">
                {data.map((adapter) => (
                  <div key={adapter.slug} className="py-2 first:pt-0 last:pb-0">
                    <ExternalAPIRow
                      adapter={adapter}
                      onToggle={(slug, enabled) => toggle({ slug, enabled })}
                      togglePending={togglePending}
                    />
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </>
  )
}
