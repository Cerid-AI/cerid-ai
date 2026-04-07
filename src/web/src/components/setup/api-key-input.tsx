// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useRef, useEffect } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Eye, EyeOff, Check, X, Loader2, ExternalLink } from "lucide-react"
import { cn } from "@/lib/utils"
import { validateProviderKey } from "@/lib/api/settings"

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
  const [status, setStatus] = useState<ValidationStatus>("idle")
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(false)

  const runValidation = useCallback(async (keyToTest: string) => {
    if (!keyToTest) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setStatus("checking")
    setError(null)

    const timeout = setTimeout(() => controller.abort(), 5000)

    try {
      const result = await validateProviderKey(provider, keyToTest)
      if (controller.signal.aborted) return
      clearTimeout(timeout)
      if (result.valid) {
        setStatus("valid")
        onKeyValidated(keyToTest === "__env__" ? "(from .env)" : keyToTest, true)
      } else {
        setStatus("invalid")
        setError(result.suggestion ?? result.error ?? "Invalid API key")
        onKeyValidated(keyToTest, false)
      }
    } catch {
      if (controller.signal.aborted) {
        setStatus("invalid")
        setError("Validation timed out — backend not responding. Is Docker running?")
        onKeyValidated(keyToTest, false)
        return
      }
      clearTimeout(timeout)
      setStatus("invalid")
      setError("Connection failed — is the backend running?")
      onKeyValidated(keyToTest, false)
    }
  }, [provider, onKeyValidated])

  const handleTest = useCallback(() => {
    const keyToTest = value.trim() || (preconfigured ? "__env__" : "")
    runValidation(keyToTest)
  }, [value, preconfigured, runValidation])

  // Auto-test preconfigured keys on mount
  useEffect(() => {
    if (preconfigured && !mountedRef.current) {
      mountedRef.current = true
      runValidation("__env__")
    }
  }, [preconfigured, runValidation])

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setValue(e.target.value)
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
            placeholder={preconfigured && !value ? "(from .env)" : (placeholder ?? "sk-...")}
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
          disabled={(!value.trim() && !preconfigured) || status === "checking"}
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
        <p className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
          <Check className="h-3 w-3" />
          Already configured in environment
        </p>
      )}
    </div>
  )
}
