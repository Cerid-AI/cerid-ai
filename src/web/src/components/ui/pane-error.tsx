// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { AlertCircle, RefreshCw } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"

interface PaneErrorProps {
  title: string
  description?: string
  onRetry?: () => void
  icon?: LucideIcon
  /** When true, centres the card in the available space rather than rendering inline. */
  fullPage?: boolean
}

/**
 * Render-state primitive for "the data fetch failed, here's how to retry."
 *
 * Sibling to `<EmptyState>` — same API shape and density.
 * Distinct from `<PaneErrorBoundary>`, which handles React render crashes.
 *
 * Inline form (default): shadcn `<Alert variant="destructive">`.
 * Full-page form: centred `<Card role="alert">` matching EmptyState density.
 */
export function PaneError({
  title,
  description,
  onRetry,
  icon: Icon = AlertCircle,
  fullPage = false,
}: PaneErrorProps) {
  if (fullPage) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card role="alert" className="border-destructive/30">
          <CardContent className="flex flex-col items-center justify-center py-10 text-center">
            <Icon className="mb-2 h-8 w-8 text-destructive" aria-hidden="true" />
            <p className="text-sm font-medium text-destructive">{title}</p>
            {description && (
              <p className="mt-1 text-xs text-muted-foreground/80">{description}</p>
            )}
            {onRetry && (
              <Button
                variant="outline"
                size="sm"
                className="mt-3"
                onClick={onRetry}
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                Retry
              </Button>
            )}
          </CardContent>
        </Card>
      </div>
    )
  }

  // NOTE: Title rendered as a styled <div>, not the shadcn <AlertTitle> (h5).
  // shadcn's AlertTitle is an h5 — when PaneError nests under a pane <h1>/<h2>,
  // axe's heading-order rule flips (level jumps h2 → h5). Using a div with the
  // same visual treatment keeps the structure axe-clean across embedding sites.
  return (
    <Alert variant="destructive">
      <Icon className="h-4 w-4" aria-hidden="true" />
      <div className="mb-1 font-medium leading-none tracking-tight">{title}</div>
      {description && <AlertDescription>{description}</AlertDescription>}
      {onRetry && (
        <div className="mt-2">
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}
    </Alert>
  )
}
