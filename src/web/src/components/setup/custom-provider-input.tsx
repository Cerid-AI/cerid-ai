// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { ChevronDown, ChevronRight, Loader2, Check, X } from "lucide-react"
interface CustomProviderState {
  name: string
  baseUrl: string
  apiKey: string
  modelId: string
  valid: boolean
}

interface CustomProviderInputProps {
  onValidated: (state: CustomProviderState) => void
}

export function CustomProviderInput({ onValidated }: CustomProviderInputProps) {
  const [expanded, setExpanded] = useState(false)
  const [name, setName] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [modelId, setModelId] = useState("")
  const [testing, setTesting] = useState(false)
  const [status, setStatus] = useState<"idle" | "valid" | "invalid">("idle")
  const [error, setError] = useState<string | null>(null)

  const handleTest = useCallback(async () => {
    if (!baseUrl.trim() || !apiKey.trim()) return
    setTesting(true)
    setError(null)
    setStatus("idle")

    try {
      const url = baseUrl.replace(/\/$/, "") + "/models"
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${apiKey.trim()}` },
        signal: AbortSignal.timeout(5000),
      })
      if (res.ok) {
        setStatus("valid")
        onValidated({ name: name || "Custom", baseUrl, apiKey, modelId, valid: true })
      } else {
        setStatus("invalid")
        setError(`Provider returned ${res.status}`)
        onValidated({ name: name || "Custom", baseUrl, apiKey, modelId, valid: false })
      }
    } catch {
      setStatus("invalid")
      setError("Connection failed — check the URL and try again")
      onValidated({ name: name || "Custom", baseUrl, apiKey, modelId, valid: false })
    } finally {
      setTesting(false)
    }
  }, [baseUrl, apiKey, name, modelId, onValidated])

  return (
    <div className="rounded-lg border">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-muted-foreground hover:text-foreground"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Add Custom Provider (OpenAI-compatible)
      </button>
      {expanded && (
        <div className="space-y-3 border-t px-3 py-3">
          <div className="space-y-1.5">
            <Label className="text-[11px]">Provider Name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Provider"
              className="h-8 text-xs"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-[11px]">API Base URL</Label>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.example.com/v1"
              className="h-8 font-mono text-xs"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-[11px]">API Key</Label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
              className="h-8 font-mono text-xs"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-[11px]">Model ID (optional)</Label>
            <Input
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              placeholder="gpt-4o"
              className="h-8 font-mono text-xs"
            />
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={handleTest}
              disabled={!baseUrl.trim() || !apiKey.trim() || testing}
            >
              {testing ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : status === "valid" ? (
                <Check className="mr-1 h-3 w-3 text-green-500" />
              ) : status === "invalid" ? (
                <X className="mr-1 h-3 w-3 text-destructive" />
              ) : null}
              Test Connection
            </Button>
            {status === "valid" && (
              <span className="text-[10px] text-green-600 dark:text-green-400">Connected</span>
            )}
          </div>
          {error && <p className="text-[10px] text-destructive">{error}</p>}
        </div>
      )}
    </div>
  )
}
