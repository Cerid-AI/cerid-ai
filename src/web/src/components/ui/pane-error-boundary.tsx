// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Component, type ErrorInfo, type ReactNode } from "react"
import type { QueryClient } from "@tanstack/react-query"
import { addBreadcrumb, captureException } from "@sentry/react"
import { AlertTriangle, RefreshCw } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"

interface Props {
  children: ReactNode
  label?: string
  /** When provided, Retry invalidates the entire React Query cache before re-rendering. */
  queryClient?: QueryClient
}

interface State {
  hasError: boolean
  error: Error | null
  retryKey: number
}

export class PaneErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, retryKey: 0 }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const label = this.props.label ?? "unknown"
    console.error(`[PaneErrorBoundary: ${label}]`, error, info)
    addBreadcrumb({
      category: "pane-error-boundary",
      message: `${label} caught render error`,
      level: "error",
      data: { pane: label, componentStack: info.componentStack?.slice(0, 500) },
    })
    captureException(error, {
      tags: { pane: label, error_class: "render_crash" },
      extra: { componentStack: info.componentStack },
    })
  }

  handleRetry = () => {
    this.props.queryClient?.invalidateQueries()
    this.setState((prev) => ({
      hasError: false,
      error: null,
      retryKey: prev.retryKey + 1,
    }))
  }

  render() {
    if (this.state.hasError) {
      const label = this.props.label
      const msg = this.state.error?.message ?? "Unknown error"
      const isMapError = msg.includes("map") || msg.includes("iterable")
      return (
        <Card className="border-destructive/30">
          <CardContent className="flex flex-col items-center justify-center py-8 text-center">
            <AlertTriangle className="mb-2 h-6 w-6 text-destructive" />
            <p className="text-sm font-medium">
              {label ? `${label} failed to render` : "Something went wrong"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {isMapError
                ? "Data loaded in an unexpected shape. Retrying will re-fetch from the server."
                : msg}
            </p>
            <Button variant="outline" size="sm" className="mt-3" onClick={this.handleRetry}>
              <RefreshCw className="mr-1.5 h-3 w-3" />
              Retry
            </Button>
          </CardContent>
        </Card>
      )
    }
    return <div key={this.state.retryKey}>{this.props.children}</div>
  }
}
