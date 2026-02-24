import { Component, type ErrorInfo, type ReactNode } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { AlertTriangle, RefreshCw } from "lucide-react"

interface Props {
  children: ReactNode
  label?: string
}

interface State {
  hasError: boolean
  error: Error | null
}

export class PaneErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[PaneErrorBoundary${this.props.label ? `: ${this.props.label}` : ""}]`, error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <Card className="border-destructive/30">
          <CardContent className="flex flex-col items-center justify-center py-8 text-center">
            <AlertTriangle className="mb-2 h-6 w-6 text-destructive" />
            <p className="text-sm font-medium">
              {this.props.label ? `${this.props.label} failed to render` : "Something went wrong"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {this.state.error?.message ?? "Unknown error"}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => this.setState({ hasError: false, error: null })}
            >
              <RefreshCw className="mr-1.5 h-3 w-3" />
              Retry
            </Button>
          </CardContent>
        </Card>
      )
    }
    return this.props.children
  }
}
