// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Eye, EyeOff, Check, X, Loader2, ExternalLink } from "lucide-react"
import { validateProviderKey } from "@/lib/api"
import { cn } from "@/lib/utils"

type ValidationStatus = "idle" | "checking" | "valid" | "invalid"

interface ApiKeyInputProps {
  provider: string
  label: string
  required?: boolean
  helpUrl?: string
  placeholder?: string
  preconfigured?: boolean
  onKeyValidated: (key: string, valid: boolean) => void
}

export function ApiKeyInput({
  provider,
  label,
  required = false,
  helpUrl,
  placeholder,
  preconfigured = false,
  onKeyValidated,
}: ApiKeyInputProps) {
  const [value, setValue] = useState("")
  const [visible, setVisible] = useState(false)
  const [status, setStatus] = useState<ValidationStatus>(preconfigured ? "valid" : "idle")
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const handleTest = useCallback(async () => {
    if (!value.trim()) return
    // Cancel any in-flight validation
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setStatus("checking")
    setError(null)

    // 5s timeout
    const timeout = setTimeout(() => controller.abort(), 5000)

    try {
      const result = await validateProviderKey(provider, value.trim())
      if (controller.signal.aborted) return
      clearTimeout(timeout)
      if (result.valid) {
        setStatus("valid")
        onKeyValidated(value.trim(), true)
      } else {
        setStatus("invalid")
        setError(result.suggestion ?? result.error ?? "Invalid API key")
        onKeyValidated(value.trim(), false)
      }
    } catch {
      if (controller.signal.aborted) {
        setStatus("invalid")
        setError("Validation timed out — backend not responding. Is Docker running?")
        onKeyValidated(value.trim(), false)
        return
      }
      clearTimeout(timeout)
      setStatus("invalid")
      setError("Connection failed — is the backend running?")
      onKeyValidated(value.trim(), false)
    }
  }, [value, provider, onKeyValidated])

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setValue(e.target.value)
    // Reset validation when key changes
    if (status !== "idle") {
      setStatus("idle")
      setError(null)
      onKeyValidated("", false)
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-sm font-medium">
          {label}
          {required && <span className="ml-1 text-destructive">*</span>}
        </Label>
        {helpUrl && (
          <a
            href={helpUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            Get a key
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>

      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input
            type={visible ? "text" : "password"}
            value={value}
            onChange={handleChange}
            placeholder={placeholder ?? "sk-..."}
            className={cn(
              "pr-9 font-mono text-xs",
              status === "valid" && "border-green-500/50",
              status === "invalid" && "border-destructive/50",
            )}
          />
          <button
            type="button"
            onClick={() => setVisible(!visible)}
            aria-label={visible ? "Hide API key" : "Show API key"}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            {visible ? (
              <EyeOff className="h-3.5 w-3.5" />
            ) : (
              <Eye className="h-3.5 w-3.5" />
            )}
          </button>
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={handleTest}
          disabled={!value.trim() || status === "checking"}
          className="shrink-0"
        >
          {status === "checking" ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : status === "valid" ? (
            <Check className="mr-1 h-3 w-3 text-green-500" />
          ) : status === "invalid" ? (
            <X className="mr-1 h-3 w-3 text-destructive" />
          ) : null}
          Test
        </Button>
      </div>

      {error && (
        <p className="text-xs text-destructive">{error}</p>
      )}
      {status === "valid" && preconfigured && !value && (
        <p className="text-xs text-green-600 dark:text-green-400">
          <Check className="mr-1 inline h-3 w-3" />
          Already configured in environment
        </p>
      )}
      {status === "valid" && !(preconfigured && !value) && (
        <p className="text-xs text-green-600 dark:text-green-400">Key validated successfully</p>
      )}
    </div>
  )
}
