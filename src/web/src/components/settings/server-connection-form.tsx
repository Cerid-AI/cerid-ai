// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// The desktop client's connection to its Cerid server: mode, address, API key.
//
// One component, two mount points — Settings → System, and the first step of
// the setup wizard. It exists because those two had to agree and one of them
// did not exist at all: onboarding ran a system check against the backend with
// no way to supply a credential, so a fresh install stalled on step 0 with
// "Could not reach backend — is Docker running?" while Docker was fine.
//
// IT PROBES BEFORE IT ASKS. A first-run user has no way to know whether their
// server enforces auth, so demanding an API key up front is a question they
// cannot answer. The probe decides:
//
//   mount
//     ├─ no desktop bridge (browser build) ──────► render nothing
//     └─ probe serverUrl with the stored key
//          ├─ auth "ok"       ──► collapsed "Connected", no key field at all
//          ├─ auth "required" ──► ask for the key, and say where it lives
//          └─ unreachable     ──► say nothing is listening + how to start it;
//                                  do NOT ask for a credential, because the
//                                  key is not the problem
//
// The key field appears in BOTH modes when it is needed. It used to render
// only in REMOTE mode, so a local-mode client had no way to send a key from
// anywhere in the product while a local server enforced auth exactly as a
// remote one does.

import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { SegmentedControl } from "@/components/ui/segmented-control"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Loader2, CheckCircle2, XCircle, KeyRound, PlugZap } from "lucide-react"
import {
  getConnectionBridge,
  type ConnectionInfo,
  type ConnectionMode,
} from "@/lib/cerid-bridge"

export const LOCAL_SERVER_URL = "http://localhost:8888"

/** What the probe found. `unknown` is deliberately distinct from `unreachable`:
    reachable-but-unanswerable must never be reported as connected. */
export type ProbeState = "probing" | "connected" | "needs-key" | "unreachable" | "unknown"

/** Map a bridge test result to the state the UI renders. Pure + exported: this
    is the decision the whole component hangs on, and it must be checkable
    without a server. */
export function probeStateFrom(result: {
  ok: boolean
  auth?: "ok" | "required" | "unknown"
  detail?: string
}): ProbeState {
  if (result.auth === "required") return "needs-key"
  if (result.ok) return "connected"
  // Not ok and not a refusal: either nothing answered, or it answered with
  // something we cannot act on. Both mean "do not ask for a key".
  return "unreachable"
}

interface ServerConnectionFormProps {
  children?: React.ReactNode
  saveLabel?: string
}

export function ServerConnectionForm({
  children,
  saveLabel = "Save & reconnect",
}: ServerConnectionFormProps) {
  const bridge = getConnectionBridge()
  const [info, setInfo] = useState<ConnectionInfo | null>(null)
  const [mode, setMode] = useState<ConnectionMode>("local")
  const [serverUrl, setServerUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [probe, setProbe] = useState<ProbeState>("probing")
  const [detail, setDetail] = useState("")
  const [saving, setSaving] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const target = mode === "local" ? LOCAL_SERVER_URL : serverUrl

  const runProbe = useCallback(
    async (url: string, key?: string) => {
      if (!bridge) return
      setProbe("probing")
      const res = await bridge.test({ serverUrl: url, apiKey: key || undefined })
      setDetail(res.detail)
      setProbe(probeStateFrom(res))
    },
    [bridge],
  )

  useEffect(() => {
    if (!bridge) return
    let cancelled = false
    void bridge.get().then((c) => {
      if (cancelled) return
      setInfo(c)
      setMode(c.mode)
      setServerUrl(c.serverUrl)
      void runProbe(c.serverUrl)
    })
    return () => {
      cancelled = true
    }
  }, [bridge, runProbe])

  const handleModeChange = useCallback((next: ConnectionMode) => {
    setMode(next)
    setProbe("unreachable")
    setDetail("")
    if (next === "remote") setServerUrl((u) => (u === LOCAL_SERVER_URL ? "" : u))
  }, [])

  const handleSave = useCallback(async () => {
    if (!bridge) return
    setSaving(true)
    try {
      // apiKey omitted → keep the stored key; sending "" would erase it.
      await bridge.set({ mode, serverUrl, ...(apiKey ? { apiKey } : {}) })
      // bridge.set reloads the renderer, so this component unmounts.
    } finally {
      setSaving(false)
    }
  }, [bridge, mode, serverUrl, apiKey])

  if (!bridge) return null

  const remote = mode === "remote"
  const urlInvalid = remote && serverUrl.trim() !== "" && !/^https?:\/\/.+/.test(serverUrl.trim())
  const canSave = !saving && (!remote || (serverUrl.trim() !== "" && !urlInvalid))
  const needsKey = probe === "needs-key"
  // Connected and nobody asked to change anything: stay out of the way.
  const collapsed = probe === "connected" && !expanded

  if (collapsed) {
    return (
      <div className="density-stack" data-testid="server-connection-form">
        <div className="flex items-center gap-2 text-xs" data-testid="connection-ok">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-green-600" />
          <span className="text-muted-foreground">
            Connected to <span className="font-medium text-foreground">{target}</span>
          </span>
          <Button
            variant="link"
            size="sm"
            className="h-auto p-0 text-xs"
            onClick={() => setExpanded(true)}
          >
            Change server
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="density-stack" data-testid="server-connection-form">
      {children}

      {probe === "probing" && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Looking for your Cerid server…
        </p>
      )}

      {probe === "unreachable" && (
        <Alert data-testid="connection-unreachable">
          <PlugZap className="h-4 w-4" />
          <AlertDescription className="text-label-xs">
            No Cerid server is answering at <span className="font-medium">{target}</span>.{" "}
            {remote
              ? "Check the address and that the machine is reachable."
              : "Start the stack with scripts/start-cerid.sh, then retry."}
            {detail && <span className="block text-muted-foreground">({detail})</span>}
          </AlertDescription>
        </Alert>
      )}

      {needsKey && (
        <Alert data-testid="connection-needs-key">
          <KeyRound className="h-4 w-4" />
          <AlertDescription className="text-label-xs">
            This server requires an API key. It is the <code>CERID_API_KEY</code> value in the
            server's <code>.env</code> file.
          </AlertDescription>
        </Alert>
      )}

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
          {urlInvalid && <p className="text-xs text-destructive">Enter a full http(s):// URL.</p>}
        </div>
      )}

      {/* Only once the server has actually asked for it — or when the operator
          opened this panel deliberately to change something. */}
      {(needsKey || expanded) && (
        <div className="space-y-1">
          <Label htmlFor="conn-key">API key</Label>
          <Input
            id="conn-key"
            type="password"
            placeholder={
              info?.hasApiKey ? "•••••••• (set — leave blank to keep)" : "paste CERID_API_KEY"
            }
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="new-password"
          />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => void runProbe(target, apiKey)}
          disabled={probe === "probing"}
          data-testid="connection-test"
        >
          {probe === "probing" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Test connection"}
        </Button>
        <Button size="sm" onClick={handleSave} disabled={!canSave} data-testid="connection-save">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : saveLabel}
        </Button>
        {probe === "connected" && (
          <span className="inline-flex items-center gap-1 text-xs text-green-600">
            <CheckCircle2 className="h-3.5 w-3.5" /> {detail}
          </span>
        )}
        {probe === "needs-key" && detail && (
          <span className="inline-flex items-center gap-1 text-xs text-destructive" role="alert">
            <XCircle className="h-3.5 w-3.5" /> {detail}
          </span>
        )}
      </div>
    </div>
  )
}
