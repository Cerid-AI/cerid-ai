// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataState — one primitive, four visual states.
 *
 * Replaces the pattern where components render a stuck "Loading..." spinner
 * even after the backend has returned an empty list, which new users mistake
 * for a broken product. Every data-fetching pane should wrap its content in
 * DataState so we handle loading / empty / error / populated consistently.
 *
 * Usage:
 *   <DataState
 *     loading={query.isLoading}
 *     error={query.error}
 *     empty={!query.data?.length}
 *     onRetry={() => query.refetch()}
 *     emptyIcon={FileText}
 *     emptyTitle="No artifacts yet"
 *     emptyDescription="Drop files above or click Upload to start."
 *   >
 *     {query.data?.map(...)}
 *   </DataState>
 */
import type { LucideIcon } from "lucide-react"
import { AlertCircle, Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"

interface DataStateProps {
  loading?: boolean
  /** Truthy → error state rendered. Can be an Error, a string, or a boolean. */
  error?: Error | string | null | boolean
  /** When true AND not loading AND no error, renders the empty block. */
  empty?: boolean
  loadingLabel?: string
  /** If omitted, error UI has no retry button. */
  onRetry?: () => void
  emptyIcon?: LucideIcon
  emptyTitle?: string
  emptyDescription?: string
  emptyAction?: { label: string; onClick: () => void }
  /** Compact styling for inline / side-panel use. Default is card-sized. */
  compact?: boolean
  children?: React.ReactNode
}

export function DataState({
  loading,
  error,
  empty,
  loadingLabel = "Loading…",
  onRetry,
  emptyIcon: EmptyIcon,
  emptyTitle,
  emptyDescription,
  emptyAction,
  compact = false,
  children,
}: DataStateProps) {
  const py = compact ? "py-4" : "py-10"

  if (loading) {
    return (
      <div
        role="status"
        aria-live="polite"
        className={`flex flex-col items-center justify-center ${py} text-center text-muted-foreground`}
      >
        <Loader2 className="mb-2 h-5 w-5 animate-spin" aria-hidden="true" />
        <span className="text-sm">{loadingLabel}</span>
      </div>
    )
  }

  if (error) {
    const msg =
      typeof error === "string"
        ? error
        : error instanceof Error
          ? error.message
          : "Something went wrong."
    return (
      <div
        role="alert"
        className={`flex flex-col items-center justify-center ${py} text-center`}
      >
        <AlertCircle className="mb-2 h-6 w-6 text-destructive/80" aria-hidden="true" />
        <p className="text-sm font-medium text-foreground">Couldn't load</p>
        <p className="mt-1 max-w-md text-xs text-muted-foreground">{msg}</p>
        {onRetry && (
          <Button size="sm" variant="outline" className="mt-3" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    )
  }

  if (empty) {
    return (
      <div
        className={`flex flex-col items-center justify-center ${py} text-center`}
      >
        {EmptyIcon && (
          <EmptyIcon
            className="mb-2 h-8 w-8 text-muted-foreground/50"
            aria-hidden="true"
          />
        )}
        {emptyTitle && (
          <p className="text-sm font-medium text-muted-foreground">{emptyTitle}</p>
        )}
        {emptyDescription && (
          <p className="mt-1 max-w-md text-xs text-muted-foreground/70">
            {emptyDescription}
          </p>
        )}
        {emptyAction && (
          <Button
            size="sm"
            variant="outline"
            className="mt-3"
            onClick={emptyAction.onClick}
          >
            {emptyAction.label}
          </Button>
        )}
      </div>
    )
  }

  return <>{children}</>
}
