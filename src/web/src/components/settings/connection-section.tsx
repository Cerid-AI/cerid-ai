// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Server connection panel — desktop-only (rendered from the System settings
// category). Lets the desktop client run the Cerid stack locally or connect to
// a remote instance on the LAN (e.g. service on a Mac Pro, client on a Mac
// Mini). Renders nothing in the browser build, where the backend is fixed.

import { useCallback, useEffect, useState } from "react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { SegmentedControl } from "@/components/ui/segmented-control"
import { Loader2, CheckCircle2, XCircle } from "lucide-react"
import {
  getConnectionBridge,
  type ConnectionInfo,
  type ConnectionMode,
} from "@/lib/cerid-bridge"

type TestState = { state: "idle" | "testing" | "ok" | "fail"; detail?: string }

export function ConnectionSection() {
  const bridge = getConnectionBridge()
  const [info, setInfo] = useState<ConnectionInfo | null>(null)
  const [mode, setMode] = useState<ConnectionMode>("local")
  const [serverUrl, setServerUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [test, setTest] = useState<TestState>({ state: "idle" })
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!bridge) return
    let cancelled = false
    bridge.get().then((c) => {
      if (cancelled) return
      setInfo(c)
      setMode(c.mode)
      setServerUrl(c.serverUrl)
    })
    return () => {
      cancelled = true
    }
  }, [bridge])

  const handleModeChange = useCallback((next: ConnectionMode) => {
    setMode(next)
    // Clear the local sentinel when moving to remote so the user types a real
    // address (and the validation/disabled state reflects an empty field).
    if (next === "remote") setServerUrl((u) => (u === "http://localhost:8888" ? "" : u))
  }, [])

  const handleTest = useCallback(async () => {
    if (!bridge) return
    setTest({ state: "testing" })
    const target = mode === "local" ? "http://localhost:8888" : serverUrl
    const res = await bridge.test({ serverUrl: target, apiKey: apiKey || undefined })
    setTest({ state: res.ok ? "ok" : "fail", detail: res.detail })
  }, [bridge, mode, serverUrl, apiKey])

  const handleSave = useCallback(async () => {
    if (!bridge) return
    setSaving(true)
    try {
      // apiKey omitted → keep the stored key; sent only when the field is touched.
      await bridge.set({
        mode,
        serverUrl,
        ...(apiKey ? { apiKey } : {}),
      })
      // bridge.set triggers a renderer reload in the main process, so this
      // component unmounts; no further state update needed.
    } finally {
      setSaving(false)
    }
  }, [bridge, mode, serverUrl, apiKey])

  // Browser build (no desktop bridge): nothing to configure.
  if (!bridge) return null

  const remote = mode === "remote"
  const urlInvalid = remote && serverUrl.trim() !== "" && !/^https?:\/\/.+/.test(serverUrl.trim())
  const canSave = !saving && (!remote || (serverUrl.trim() !== "" && !urlInvalid))

  return (
    <Card data-testid="connection-section">
      <CardHeader className="pb-2">
        <span className="text-label-xs uppercase tracking-wider text-muted-foreground">
          Server Connection
        </span>
      </CardHeader>
      <CardContent className="density-stack">
        <p className="text-xs text-muted-foreground">
          Run the Cerid stack on this Mac, or connect to a Cerid instance on another machine on your
          network.
        </p>

        <SegmentedControl<ConnectionMode>
          value={mode}
          onChange={handleModeChange}
          options={[
            { value: "local", label: "This Mac" },
            { value: "remote", label: "Remote server" },
          ]}
          ariaLabel="Connection mode"
        />

        {remote && (
          <div className="density-stack">
            <div className="space-y-1">
              <Label htmlFor="conn-url">Server URL</Label>
              <Input
                id="conn-url"
                placeholder="https://macpro.local  or  http://192.168.1.50:8888"
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                aria-invalid={urlInvalid}
                autoComplete="off"
              />
              {urlInvalid && (
                <p className="text-xs text-destructive">Enter a full http(s):// URL.</p>
              )}
            </div>
            <div className="space-y-1">
              <Label htmlFor="conn-key">API key</Label>
              <Input
                id="conn-key"
                type="password"
                placeholder={info?.hasApiKey ? "•••••••• (set — leave blank to keep)" : "required when the server enforces auth"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="new-password"
              />
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleTest} disabled={test.state === "testing"}>
            {test.state === "testing" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Test connection"}
          </Button>
          <Button size="sm" onClick={handleSave} disabled={!canSave} data-testid="connection-save">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save & reconnect"}
          </Button>
          {test.state === "ok" && (
            <span className="inline-flex items-center gap-1 text-xs text-green-600">
              <CheckCircle2 className="h-3.5 w-3.5" /> {test.detail}
            </span>
          )}
          {test.state === "fail" && (
            <span className="inline-flex items-center gap-1 text-xs text-destructive" role="alert">
              <XCircle className="h-3.5 w-3.5" /> {test.detail}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
