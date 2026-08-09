// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useCallback, useEffect, useRef, useState } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Card } from "@/components/ui/card"
import { Check, X, Loader2, ExternalLink, Eye, EyeOff, AlertTriangle } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  fetchHFTokenStatus,
  putHFToken,
  testHFToken,
  type HFTokenStatus,
  type HFTokenTestResult,
} from "@/lib/api/settings"

const GATED_MODELS = [
  {
    id: "pyannote/speaker-diarization-3.1",
    url: "https://hf.co/pyannote/speaker-diarization-3.1",
    label: "Speaker Diarization 3.1",
  },
  {
    id: "pyannote/segmentation-3.0",
    url: "https://hf.co/pyannote/segmentation-3.0",
    label: "Segmentation 3.0",
  },
]

type Status = "idle" | "checking" | "valid" | "invalid"

interface HFTokenStepProps {
  /** Called when token is saved AND all gates are confirmed accepted. */
  onComplete?: () => void
  /** Render mode: 'wizard' (full step UI) or 'compact' (settings panel). */
  mode?: "wizard" | "compact"
}

export function HFTokenStep({ onComplete, mode = "wizard" }: HFTokenStepProps) {
  const [stored, setStored] = useState<HFTokenStatus | null>(null)
  const [value, setValue] = useState("")
  const [visible, setVisible] = useState(false)
  const [status, setStatus] = useState<Status>("idle")
  const [result, setResult] = useState<HFTokenTestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    fetchHFTokenStatus().then(setStored).catch(() => setStored(null))
  }, [])

  const runTest = useCallback(async (tokenToTest?: string) => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setStatus("checking")
    setError(null)
    setResult(null)

    try {
      const res = await testHFToken(tokenToTest)
      if (controller.signal.aborted) return
      setResult(res)
      if (res.valid) {
        setStatus("valid")
        const access = res.gated_model_access ?? {}
        const allAccepted = GATED_MODELS.every((m) => access[m.id] === true)
        if (allAccepted) onComplete?.()
      } else {
        setStatus("invalid")
        setError(res.error ?? "Invalid token")
      }
    } catch (e) {
      if (controller.signal.aborted) return
      setStatus("invalid")
      setError(e instanceof Error ? e.message : "Network error")
    }
  }, [onComplete])

  const handleSave = useCallback(async () => {
    if (!value.trim()) return
    setStatus("checking")
    setError(null)
    try {
      const saved = await putHFToken(value.trim())
      setStored(saved)
      // Immediately test the freshly-stored token's gated-model access.
      await runTest(value.trim())
      setValue("")
    } catch (e) {
      setStatus("invalid")
      setError(e instanceof Error ? e.message : "Save failed")
    }
  }, [value, runTest])

  const hasStored = stored?.configured ?? false
  const access = result?.gated_model_access

  return (
    <div className="space-y-4" data-testid="hf-token-step">
      {mode === "wizard" && (
        <div>
          <h3 className="text-lg font-semibold">HuggingFace Token (Optional)</h3>
          <p className="text-sm text-muted-foreground mt-1">
            Required only if you'll use meeting transcription with speaker
            identification. Skip this step if you don't plan to upload audio.
          </p>
        </div>
      )}

      <Card className="p-4 space-y-3 border-amber-500/30 bg-amber-500/5">
        <div className="flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 flex-shrink-0" />
          <div className="text-sm space-y-2">
            <p className="font-medium">Accept these two model licenses first:</p>
            <ul className="space-y-1 ml-1">
              {GATED_MODELS.map((m) => {
                const accepted = access?.[m.id]
                return (
                  <li key={m.id} className="flex items-center gap-2">
                    {accepted === true && (
                      <Check className="w-3.5 h-3.5 text-green-500" />
                    )}
                    {accepted === false && (
                      <X className="w-3.5 h-3.5 text-amber-500" />
                    )}
                    {accepted === undefined && (
                      <span className="w-3.5 h-3.5 rounded-full border border-muted-foreground/30" />
                    )}
                    <a
                      href={m.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-500 hover:underline inline-flex items-center gap-1"
                    >
                      {m.label}
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </li>
                )
              })}
            </ul>
          </div>
        </div>
      </Card>

      <div className="space-y-2">
        <Label htmlFor="hf-token-input">
          {hasStored ? "Replace token" : "Paste your token"}
        </Label>
        {hasStored && (
          <p className="text-xs text-muted-foreground">
            Currently stored, ending in <code className="font-mono">…{stored?.last4}</code>
            {stored?.updated_at && ` · saved ${new Date(stored.updated_at).toLocaleDateString()}`}
          </p>
        )}
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Input
              id="hf-token-input"
              type={visible ? "text" : "password"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="hf_..."
              autoComplete="off"
              spellCheck={false}
            />
            <button
              type="button"
              onClick={() => setVisible(!visible)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              aria-label={visible ? "Hide token" : "Show token"}
            >
              {visible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          <Button
            onClick={handleSave}
            disabled={!value.trim() || status === "checking"}
            data-testid="hf-token-save"
          >
            {status === "checking" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              "Save"
            )}
          </Button>
          {hasStored && (
            <Button
              variant="outline"
              onClick={() => runTest()}
              disabled={status === "checking"}
              data-testid="hf-token-test-stored"
            >
              Test
            </Button>
          )}
        </div>

        <a
          href="https://huggingface.co/settings/tokens"
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-blue-500 hover:underline inline-flex items-center gap-1"
        >
          Create a token at huggingface.co/settings/tokens
          <ExternalLink className="w-3 h-3" />
        </a>
      </div>

      {status === "valid" && result?.gated_model_access && (
        <div className={cn(
          "text-sm p-2 rounded border",
          GATED_MODELS.every((m) => result.gated_model_access?.[m.id])
            ? "border-green-500/30 bg-green-500/5"
            : "border-amber-500/30 bg-amber-500/5"
        )}>
          {GATED_MODELS.every((m) => result.gated_model_access?.[m.id])
            ? "Token valid; all gated models accepted."
            : "Token valid, but at least one model license still needs accepting (see above)."}
        </div>
      )}

      {status === "invalid" && error && (
        <div className="text-sm text-red-500" role="alert">
          {error}
        </div>
      )}
    </div>
  )
}
