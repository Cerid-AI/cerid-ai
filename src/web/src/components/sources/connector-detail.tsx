// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ConnectorDetail — dialog showing status + auth/disconnect flow for a connector.
 *
 * Layout mirrors SourceDetailPane: Dialog shell > DialogHeader (liquid-glass) > sections.
 * Three auth flows are supported based on the auth_kind field:
 *   google_oauth / oauth  → auth_url rendered as visible anchor
 *   msal                  → device_code + verification_uri rendered as visible text/anchor
 *   tcc                   → settings_url rendered as visible text (not auto-opened)
 *
 * Polling: after startConnectorAuth, polls getConnectorAuthStatus every 3 s until
 * completed === true, then invalidates ["connectors"] and calls onClose.
 * The interval is cleared on unmount to prevent leaks.
 */

import { useState, useEffect, useRef } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, XCircle, Minus } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  startConnectorAuth,
  getConnectorAuthStatus,
  disconnectConnector,
  type OAuthStartResponse,
} from "@/lib/api/connectors"
import type { ConnectorStatusExt } from "./source-rows"

// ---------------------------------------------------------------------------
// Public props
// ---------------------------------------------------------------------------

interface ConnectorDetailProps {
  connector: ConnectorStatusExt
  open: boolean
  onClose: () => void
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

export function ConnectorDetail({ connector, open, onClose }: ConnectorDetailProps) {
  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="max-w-lg p-0">
        {open && (
          <ConnectorDetailInner
            key={connector.slug}
            connector={connector}
            onClose={onClose}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Inner content (re-mounts on slug change via key)
// ---------------------------------------------------------------------------

function ConnectorDetailInner({
  connector,
  onClose,
}: {
  connector: ConnectorStatusExt
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [authFlow, setAuthFlow] = useState<OAuthStartResponse | null>(null)
  const [authError, setAuthError] = useState<string | null>(null)
  const [authBusy, setAuthBusy] = useState(false)
  const [disconnectResult, setDisconnectResult] = useState<string | null>(null)
  const [disconnectBusy, setDisconnectBusy] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Clean up polling interval on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [])

  const startPolling = (slug: string) => {
    if (pollRef.current !== null) clearInterval(pollRef.current)
    pollRef.current = setInterval(() => {
      void getConnectorAuthStatus(slug).then((res) => {
        if (res.completed) {
          clearInterval(pollRef.current!)
          pollRef.current = null
          qc.invalidateQueries({ queryKey: ["connectors"] })
          onClose()
        }
      })
    }, 3000)
  }

  const handleConnect = async () => {
    setAuthBusy(true)
    setAuthError(null)
    try {
      const flow = await startConnectorAuth(connector.slug)
      setAuthFlow(flow)
      startPolling(connector.slug)
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Failed to start auth")
    } finally {
      setAuthBusy(false)
    }
  }

  const handleDisconnect = async () => {
    setDisconnectBusy(true)
    try {
      const res = await disconnectConnector(connector.slug)
      setDisconnectResult(res.detail)
      qc.invalidateQueries({ queryKey: ["connectors"] })
    } catch (err) {
      setDisconnectResult(err instanceof Error ? err.message : "Disconnect failed")
    } finally {
      setDisconnectBusy(false)
    }
  }

  return (
    <div className="space-y-0">
      {/* Header — Liquid Glass */}
      <DialogHeader className="liquid-glass rounded-t-lg px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <DialogTitle className="text-base font-medium">
              {connector.display_name}
            </DialogTitle>
            <div className="mt-0.5 flex items-center gap-2 text-label-xs text-muted-foreground">
              <span>{connector.auth_kind}</span>
              <StatusPill configured={connector.data_source_configured} />
            </div>
          </div>
        </div>
      </DialogHeader>

      <div className="space-y-5 px-5 py-4">
        {/* Explainer — what connecting this actually does (P0-C.4).
            Fields are optional so older backend payloads render without it. */}
        {(connector.imports_desc || connector.sync_semantics || connector.lands_in) && (
          <Section title="How this connector works">
            <dl className="space-y-1.5">
              {connector.imports_desc && (
                <ExplainerRow label="Reads" value={connector.imports_desc} />
              )}
              {connector.sync_semantics && (
                <ExplainerRow label="Sync" value={connector.sync_semantics} />
              )}
              {connector.lands_in && (
                <ExplainerRow label="Data destination" value={connector.lands_in} />
              )}
            </dl>
          </Section>
        )}

        {/* Status section */}
        <Section title="Status">
          <ul className="space-y-1">
            <StatusRow label="Environment variables" ok={connector.env_complete} />
            <StatusRow label="Feature enabled" ok={connector.feature_enabled} />
            <StatusRow label="Connected" ok={connector.data_source_configured} />
            {connector.sibling_reachable !== null && (
              <StatusRow label="Sibling service reachable" ok={connector.sibling_reachable} />
            )}
          </ul>

          {/* Missing env list */}
          {!connector.env_complete && connector.missing_env.length > 0 && (
            <div className="mt-2 rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 dark:border-amber-800/40 dark:bg-amber-900/20">
              <p className="text-label-xs font-medium text-amber-700 dark:text-amber-400">
                Missing environment variables:
              </p>
              <ul className="mt-1 space-y-0.5">
                {connector.missing_env.map((v) => (
                  <li key={v} className="font-mono text-label-xs text-amber-600 dark:text-amber-300">
                    {v}
                  </li>
                ))}
              </ul>
              {connector.instruction_doc && (
                <p className="mt-1 text-label-xs text-muted-foreground">
                  See: <span className="font-mono">{connector.instruction_doc}</span>
                </p>
              )}
            </div>
          )}
        </Section>

        {/* Action section: Connect or Disconnect */}
        {connector.data_source_configured ? (
          <Section title="Connection">
            {disconnectResult ? (
              <p className="text-sm text-muted-foreground">{disconnectResult}</p>
            ) : (
              <Button
                size="sm"
                variant="destructive"
                disabled={disconnectBusy}
                onClick={() => { void handleDisconnect() }}
                className="cerid-press"
              >
                {disconnectBusy ? "Disconnecting…" : "Disconnect"}
              </Button>
            )}
          </Section>
        ) : (
          <Section title="Connection">
            {!authFlow && (
              <Button
                size="sm"
                disabled={authBusy || !connector.env_complete || !connector.feature_enabled}
                onClick={() => { void handleConnect() }}
                className="cerid-press"
              >
                {authBusy ? "Starting…" : "Connect"}
              </Button>
            )}

            {authError && (
              <p className="mt-2 text-label-xs text-destructive">{authError}</p>
            )}

            {authFlow && <AuthFlowPanel flow={authFlow} />}
          </Section>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Auth flow renderer — covers three cases, never auto-navigates
// ---------------------------------------------------------------------------

function AuthFlowPanel({ flow }: { flow: OAuthStartResponse }) {
  return (
    <div className="mt-2 space-y-2">
      <p className="text-sm text-foreground">{flow.instructions}</p>

      {/* oauth / google_oauth: auth_url as visible anchor */}
      {flow.auth_url && (
        <div>
          <p className="text-label-xs text-muted-foreground">Authorization URL (open in your browser):</p>
          <a
            href={flow.auth_url}
            target="_blank"
            rel="noopener noreferrer"
            className="break-all text-label-xs text-primary underline"
          >
            {flow.auth_url}
          </a>
        </div>
      )}

      {/* msal: device_code + verification_uri */}
      {flow.device_code && (
        <div className="space-y-1">
          <p className="text-label-xs text-muted-foreground">Device code:</p>
          <p className="font-mono text-base font-semibold tracking-widest">{flow.device_code}</p>
        </div>
      )}
      {flow.verification_uri && (
        <div>
          <p className="text-label-xs text-muted-foreground">Verification URL:</p>
          <a
            href={flow.verification_uri}
            target="_blank"
            rel="noopener noreferrer"
            className="break-all text-label-xs text-primary underline"
          >
            {flow.verification_uri}
          </a>
        </div>
      )}

      {/* tcc: settings_url as visible text (not a real http link; OS-handled) */}
      {flow.settings_url && (
        <div>
          <p className="text-label-xs text-muted-foreground">Settings path:</p>
          <span className="break-all font-mono text-label-xs">{flow.settings_url}</span>
        </div>
      )}

      {flow.expires_in && (
        <p className="text-label-xs text-muted-foreground">Expires in {flow.expires_in} seconds. Waiting for completion…</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium text-foreground">{title}</h3>
      {children}
    </div>
  )
}

function ExplainerRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[7rem_1fr] gap-2">
      <dt className="text-label-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="text-label-xs leading-relaxed text-foreground/90">{value}</dd>
    </div>
  )
}

function StatusPill({ configured }: { configured: boolean }) {
  const color = configured
    ? "bg-emerald-500/10 text-emerald-500"
    : "bg-muted text-muted-foreground"
  return (
    <span className={cn("rounded-full px-1.5 py-0.5 text-label-xs", color)}>
      {configured ? "connected" : "available"}
    </span>
  )
}

function StatusRow({ label, ok }: { label: string; ok: boolean | null }) {
  const Icon = ok === true
    ? CheckCircle2
    : ok === false
      ? XCircle
      : Minus
  const color = ok === true
    ? "text-emerald-500"
    : ok === false
      ? "text-destructive"
      : "text-muted-foreground"
  return (
    <li className="flex items-center gap-1.5 text-label-xs">
      <Icon className={cn("h-3.5 w-3.5 shrink-0", color)} aria-hidden="true" />
      <span className={ok === false ? "text-muted-foreground" : undefined}>{label}</span>
    </li>
  )
}
