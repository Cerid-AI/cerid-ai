// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { authSetApiKey, authDeleteApiKey, authApiKeyStatus, authUsage } from "@/lib/api"
import { Key, Trash2, Loader2, CheckCircle2 } from "lucide-react"
import type { UsageInfo } from "@/lib/types"

export function ApiKeySettings() {
  const [hasKey, setHasKey] = useState(false)
  const [newKey, setNewKey] = useState("")
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const [usage, setUsage] = useState<UsageInfo | null>(null)

  useEffect(() => {
    authApiKeyStatus().then((s) => setHasKey(s.has_key)).catch(() => {})
    authUsage().then(setUsage).catch(() => {})
  }, [])

  const saveKey = useCallback(async () => {
    if (!newKey.trim()) return
    setSaving(true)
    setMessage("")
    try {
      await authSetApiKey(newKey.trim())
      setHasKey(true)
      setNewKey("")
      setMessage("API key saved")
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to save key")
    } finally {
      setSaving(false)
    }
  }, [newKey])

  const removeKey = useCallback(async () => {
    setSaving(true)
    setMessage("")
    try {
      await authDeleteApiKey()
      setHasKey(false)
      setMessage("API key removed")
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to remove key")
    } finally {
      setSaving(false)
    }
  }, [])

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-medium">OpenRouter API Key</h3>
        <p className="text-xs text-muted-foreground">
          Bring your own key for chat. When not set, the shared key is used.
        </p>
      </div>

      {hasKey ? (
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 text-green-500" />
          <span className="text-sm">API key configured</span>
          <Button variant="ghost" size="sm" onClick={removeKey} disabled={saving}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ) : (
        <div className="flex gap-2">
          <Input
            type="password"
            placeholder="sk-or-..."
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            className="flex-1"
          />
          <Button size="sm" onClick={saveKey} disabled={saving || !newKey.trim()}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Key className="h-4 w-4" />}
          </Button>
        </div>
      )}

      {message && <p className="text-xs text-muted-foreground">{message}</p>}

      {usage && (
        <div className="mt-4 space-y-1">
          <h3 className="text-sm font-medium">Usage ({usage.month})</h3>
          <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
            <span>Queries: {usage.queries}</span>
            <span>Ingestions: {usage.ingestions}</span>
          </div>
        </div>
      )}
    </div>
  )
}
