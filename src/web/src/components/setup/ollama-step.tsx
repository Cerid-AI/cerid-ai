// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Cpu, ExternalLink, Loader2, Check, Download } from "lucide-react"
import { pullOllamaModel } from "@/lib/api"

interface OllamaState {
  detected: boolean
  enabled: boolean
  model: string | null
  pulling: boolean
}

interface OllamaStepProps {
  ollamaDetected: boolean
  ollamaModels: string[]
  state: OllamaState
  onChange: (state: OllamaState) => void
}

const RECOMMENDED_MODEL = "llama3.2:3b"
const RECOMMENDED_MODEL_SIZE = "2.0 GB"

export function OllamaStep({ ollamaDetected, ollamaModels, state, onChange }: OllamaStepProps) {
  const [pullProgress, setPullProgress] = useState<string | null>(null)
  const [pullError, setPullError] = useState<string | null>(null)

  const handlePull = useCallback(async () => {
    onChange({ ...state, pulling: true })
    setPullError(null)
    setPullProgress("Starting download...")

    try {
      await pullOllamaModel(RECOMMENDED_MODEL)
      onChange({ ...state, pulling: false, model: RECOMMENDED_MODEL })
      setPullProgress(null)
    } catch {
      setPullError("Failed to pull model — check Ollama is running")
      onChange({ ...state, pulling: false })
      setPullProgress(null)
    }
  }, [state, onChange])

  const hasRecommendedModel = ollamaModels.some(
    (m) => m === RECOMMENDED_MODEL || m.startsWith("llama3.2"),
  )

  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Cpu className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Local LLM</h3>
      <p className="mb-4 text-center text-xs text-muted-foreground">Optional</p>

      <div className="space-y-4">
        <p className="text-center text-sm text-muted-foreground">
          Ollama runs AI models locally for free. Cerid uses it for background tasks like
          verification and claim extraction — your main chat still uses OpenRouter.
        </p>

        {/* Connection Status */}
        <div className="flex items-center justify-center gap-2">
          {ollamaDetected ? (
            <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">
              <Check className="mr-1 h-3 w-3" />
              Connected
            </Badge>
          ) : (
            <Badge variant="outline" className="border-yellow-500/30 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400">
              Not detected
            </Badge>
          )}
        </div>

        {ollamaDetected ? (
          <>
            {/* Models */}
            {ollamaModels.length > 0 && (
              <div className="rounded-lg border bg-card p-3">
                <p className="mb-2 text-[11px] font-medium text-muted-foreground">
                  Installed Models
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {ollamaModels.map((model) => (
                    <Badge key={model} variant="secondary" className="text-[10px]">
                      {model}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {/* Pull recommendation */}
            {!hasRecommendedModel && !state.model && (
              <div className="rounded-lg border bg-card p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium">{RECOMMENDED_MODEL}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {RECOMMENDED_MODEL_SIZE} — best balance of speed and quality for pipeline tasks
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handlePull}
                    disabled={state.pulling}
                    className="shrink-0"
                  >
                    {state.pulling ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : (
                      <Download className="mr-1 h-3 w-3" />
                    )}
                    Pull
                  </Button>
                </div>
                {pullProgress && (
                  <p className="mt-2 text-[10px] text-muted-foreground">{pullProgress}</p>
                )}
                {pullError && (
                  <p className="mt-2 text-[10px] text-destructive">{pullError}</p>
                )}
              </div>
            )}

            {state.model && (
              <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-3 text-center text-xs text-green-600 dark:text-green-400">
                <Check className="mr-1 inline h-3 w-3" />
                {state.model} ready
              </div>
            )}

            {/* Enable toggle */}
            <div className="flex items-center justify-between rounded-lg border bg-card px-3 py-2.5">
              <div>
                <Label className="text-xs font-medium">Enable for pipeline tasks</Label>
                <p className="text-[10px] text-muted-foreground">
                  Verification, routing, claim extraction
                </p>
              </div>
              <Switch
                checked={state.enabled}
                onCheckedChange={(checked) => onChange({ ...state, enabled: checked })}
              />
            </div>
          </>
        ) : (
          /* Not detected */
          <div className="space-y-2">
            <div className="rounded-lg border bg-card p-3">
              <p className="text-xs text-muted-foreground">
                Ollama is not running on this machine. You can install it now or enable it later
                in Settings.
              </p>
            </div>
            <a
              href="https://ollama.com/download"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-2 rounded-lg border bg-card p-3 text-xs font-medium text-brand hover:bg-brand/5"
            >
              Install Ollama
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        )}
      </div>
    </>
  )
}
