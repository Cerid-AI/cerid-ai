// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import {
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  Server,
  Lock,
  Copy,
  Check,
  AlertTriangle,
  Plus,
  RotateCcw,
  Trash2,
  Loader2,
} from "lucide-react"
import type { ServerSettings } from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { cn } from "@/lib/utils"
import {
  fetchMcpServers,
  addMcpServer,
  deleteMcpServer,
  reconnectMcpServer,
  type McpServerInfo,
  type McpServerAddRequest,
} from "@/lib/api/governance"
import { logSwallowedError } from "@/lib/log-swallowed"
import { SectionHeading, LabelWithInfo, Row } from "./settings-primitives"
import type { SectionKey } from "./settings-primitives"

interface GovernanceSectionProps {
  settings: ServerSettings
  sections: Record<SectionKey, boolean>
  toggleSection: (key: SectionKey) => void
}

const SERVER_NAME_PATTERN = /^[a-z][a-z0-9_-]{1,30}$/

/**
 * Sprint 1 governance surfaces (MCP_CLIENT_MODE, STRICT_AGENTS_ONLY,
 * external MCP server list).
 *
 * MCP_CLIENT_MODE / STRICT_AGENTS_ONLY are deployment-time env-var
 * controls — read-only with copy-to-clipboard for the env line. The
 * external MCP server list supports full CRUD via the
 * `/mcp-servers` REST endpoints (add stdio + sse, reconnect, delete).
 */
export function GovernanceSection({ settings, sections, toggleSection }: GovernanceSectionProps) {
  const mode = settings.mcp_client_mode ?? "permissive"
  const allowlist = settings.mcp_client_allowlist ?? []
  const strictAgents = settings.strict_agents_only ?? false

  const [servers, setServers] = useState<McpServerInfo[]>([])
  const [serversError, setServersError] = useState<string | null>(null)
  const [serversLoading, setServersLoading] = useState(false)
  const [showAdd, setShowAdd] = useState(false)

  const reload = useCallback(async () => {
    setServersLoading(true)
    setServersError(null)
    try {
      const res = await fetchMcpServers()
      setServers(res.servers)
    } catch (err) {
      setServersError(err instanceof Error ? err.message : "fetch failed")
      logSwallowedError(err, "governance.fetchMcpServers")
    } finally {
      setServersLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  return (
    <>
      {/* -- MCP Client Mode -- */}
      <SectionHeading
        icon={modeIcon(mode)}
        label="External MCP Client Mode"
        open={sections.governance_mcp}
        onToggle={() => toggleSection("governance_mcp")}
      />
      {sections.governance_mcp && (
        <Card className="mb-4">
          <CardContent className="grid gap-3 pt-4">
            <div className="flex items-center justify-between">
              <LabelWithInfo
                label="Mode"
                info="Controls whether the agent runtime can call external MCP servers. Permissive = all configured servers callable. Allowlist = only servers in MCP_CLIENT_ALLOWLIST. Disabled = no external MCP calls (kill switch for compliance-sensitive deployments)."
              />
              <ModeBadge mode={mode} />
            </div>
            {mode === "allowlist" && (
              <div>
                <LabelWithInfo label="Allowlist" info="Servers callable in allowlist mode (comma-separated in MCP_CLIENT_ALLOWLIST)." />
                {allowlist.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {allowlist.map((s) => (
                      <Badge key={s} variant="outline" className="font-mono text-xs">{s}</Badge>
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-xs text-yellow-600 dark:text-yellow-400">
                    Allowlist mode is on but MCP_CLIENT_ALLOWLIST is empty — every external MCP call will be denied.
                  </p>
                )}
              </div>
            )}
            <EnvCopyRow envName="MCP_CLIENT_MODE" value={mode} />
            <EnvCopyRow envName="MCP_CLIENT_ALLOWLIST" value={allowlist.join(",")} />
            <p className="text-xs text-muted-foreground">
              Per-call audit: every external MCP tool call (ok / fail / denied) emits a structured log entry + Sentry breadcrumb. Logged at the <code className="font-mono">ai-companion.mcp_client_policy</code> logger.
            </p>
          </CardContent>
        </Card>
      )}

      {/* -- Strict-Agents Mode -- */}
      <SectionHeading
        icon={strictAgents ? ShieldX : ShieldCheck}
        label="Strict-Agents Mode"
        open={sections.governance_agents}
        onToggle={() => toggleSection("governance_agents")}
      />
      {sections.governance_agents && (
        <Card className="mb-4">
          <CardContent className="grid gap-3 pt-4">
            <div className="flex items-center justify-between">
              <LabelWithInfo
                label="Status"
                info="When enabled, every /custom-agents endpoint returns 403 — the kill switch for regulated deployments. Built-in 10 specialist agents remain available."
              />
              {strictAgents ? (
                <Badge className="bg-red-500/10 text-red-600 border border-red-500/30 dark:text-red-400">
                  ENABLED — Custom agents disabled
                </Badge>
              ) : (
                <Badge variant="outline" className="text-muted-foreground">
                  Disabled — custom agents available
                </Badge>
              )}
            </div>
            <EnvCopyRow envName="STRICT_AGENTS_ONLY" value={strictAgents ? "true" : "false"} />
          </CardContent>
        </Card>
      )}

      {/* -- External MCP Servers -- */}
      <SectionHeading
        icon={Server}
        label="External MCP Servers"
        open={sections.governance_servers}
        onToggle={() => toggleSection("governance_servers")}
      />
      {sections.governance_servers && (
        <Card className="mb-4">
          <CardContent className="grid gap-3 pt-4">
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                Configured external MCP servers — discovered tools surface as <code className="font-mono">ext_*</code> for agents.
              </p>
              <Button size="sm" variant="outline" onClick={() => setShowAdd(true)} className="h-7 gap-1 text-xs">
                <Plus className="h-3 w-3" /> Add Server
              </Button>
            </div>
            {serversError ? (
              <p className="text-xs text-yellow-600 dark:text-yellow-400 flex items-center gap-1">
                <AlertTriangle className="h-3 w-3" /> Could not load /mcp-servers: {serversError}
              </p>
            ) : servers.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No external MCP servers registered. Click <strong>Add Server</strong> to register one, or set <code className="font-mono">MCP_SERVERS_CONFIG</code> in your .env (JSON array).
              </p>
            ) : (
              <div className="grid gap-2">
                {servers.map((s) => (
                  <ServerRow
                    key={s.name}
                    server={s}
                    mode={mode}
                    allowlist={allowlist}
                    onChanged={reload}
                  />
                ))}
                <Row label="Total servers" value={String(servers.length)} info="Configured external MCP servers" />
                <Row
                  label="Total external tools"
                  value={String(servers.reduce((acc, s) => acc + (s.tool_count ?? 0), 0))}
                  info="Total ext_* tools available to agents from connected servers"
                />
              </div>
            )}
            {serversLoading && (
              <p className="text-[11px] text-muted-foreground flex items-center gap-1">
                <Loader2 className="h-3 w-3 animate-spin" /> Refreshing…
              </p>
            )}
          </CardContent>
        </Card>
      )}

      <AddServerDialog
        open={showAdd}
        onOpenChange={setShowAdd}
        onAdded={async () => {
          setShowAdd(false)
          await reload()
        }}
      />
    </>
  )
}

function modeIcon(mode: string) {
  switch (mode) {
    case "disabled": return ShieldX
    case "allowlist": return ShieldAlert
    default: return ShieldCheck
  }
}

function ModeBadge({ mode }: { mode: string }) {
  const styles =
    mode === "disabled"
      ? "bg-red-500/10 text-red-600 border-red-500/30 dark:text-red-400"
      : mode === "allowlist"
      ? "bg-yellow-500/10 text-yellow-600 border-yellow-500/30 dark:text-yellow-400"
      : "bg-green-500/10 text-green-600 border-green-500/30 dark:text-green-400"
  return (
    <Badge className={cn("uppercase tracking-wide font-mono text-[10px]", styles)}>
      {mode}
    </Badge>
  )
}

function ServerRow({
  server,
  mode,
  allowlist,
  onChanged,
}: {
  server: McpServerInfo
  mode: string
  allowlist: string[]
  onChanged: () => Promise<void> | void
}) {
  const [pending, setPending] = useState<"reconnect" | "delete" | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const wouldDeny =
    mode === "disabled" ||
    (mode === "allowlist" && !allowlist.includes(server.name.toLowerCase()))
  const statusBadge =
    server.status === "connected" ? (
      <Badge className="bg-green-500/10 text-green-600 border-green-500/30 dark:text-green-400 text-[10px]">connected</Badge>
    ) : server.status === "error" ? (
      <Badge className="bg-red-500/10 text-red-600 border-red-500/30 dark:text-red-400 text-[10px]">error</Badge>
    ) : (
      <Badge variant="outline" className="text-[10px] text-muted-foreground">disconnected</Badge>
    )

  const onReconnect = async () => {
    setPending("reconnect")
    setActionError(null)
    try {
      await reconnectMcpServer(server.name)
      await onChanged()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "reconnect failed")
      logSwallowedError(err, "governance.reconnectMcpServer", { name: server.name })
    } finally {
      setPending(null)
    }
  }

  const onDelete = async () => {
    setPending("delete")
    setActionError(null)
    try {
      await deleteMcpServer(server.name)
      await onChanged()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "delete failed")
      logSwallowedError(err, "governance.deleteMcpServer", { name: server.name })
    } finally {
      setPending(null)
      setConfirmDelete(false)
    }
  }

  return (
    <div className="rounded-md border border-border p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono font-medium truncate">{server.name}</span>
          <span className="text-muted-foreground">({server.transport})</span>
        </div>
        <div className="flex items-center gap-1">
          {wouldDeny && (
            <Badge className="bg-red-500/10 text-red-600 border-red-500/30 dark:text-red-400 text-[10px]" title="Would be denied by current MCP_CLIENT_MODE">
              <Lock className="mr-0.5 h-2.5 w-2.5" /> denied
            </Badge>
          )}
          {statusBadge}
          <Button
            size="icon"
            variant="ghost"
            className="h-6 w-6"
            onClick={onReconnect}
            disabled={pending !== null}
            title="Reconnect + re-discover tools"
            aria-label={`Reconnect ${server.name}`}
          >
            {pending === "reconnect" ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="h-6 w-6 text-red-600 hover:text-red-700 hover:bg-red-500/10 dark:text-red-400"
            onClick={() => setConfirmDelete(true)}
            disabled={pending !== null}
            title="Disconnect + remove"
            aria-label={`Remove ${server.name}`}
          >
            {pending === "delete" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
          </Button>
        </div>
      </div>
      {server.error && (
        <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">{server.error}</p>
      )}
      {actionError && (
        <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">{actionError}</p>
      )}
      <div className="mt-1 flex items-center gap-2 text-muted-foreground">
        <span>{server.tool_count} tool{server.tool_count !== 1 ? "s" : ""}</span>
        {server.tools && server.tools.length > 0 && (
          <span className="truncate font-mono">{server.tools.slice(0, 4).join(", ")}{server.tools.length > 4 ? "…" : ""}</span>
        )}
      </div>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {server.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Disconnects the session and removes the server from <code className="font-mono">/mcp-servers</code>. Its
              {" "}<code className="font-mono">ext_*</code> tools will no longer be available to agents.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending !== null}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => { e.preventDefault(); void onDelete() }}
              disabled={pending !== null}
              className="bg-red-600 hover:bg-red-700"
            >
              {pending === "delete" ? "Removing…" : "Remove"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function EnvCopyRow({ envName, value }: { envName: string; value: string }) {
  const [copied, setCopied] = useState(false)
  const line = `${envName}=${value}`
  const onCopy = () => {
    navigator.clipboard?.writeText(line).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    }).catch((err) => logSwallowedError(err, "clipboard.writeText", { envName }))
  }
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/30 px-2 py-1.5">
      <code className="font-mono text-xs text-muted-foreground truncate">{line}</code>
      <Button variant="ghost" size="icon" onClick={onCopy} className="h-6 w-6" aria-label={`Copy ${envName}`}>
        {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
      </Button>
    </div>
  )
}

function AddServerDialog({
  open,
  onOpenChange,
  onAdded,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onAdded: () => Promise<void> | void
}) {
  const [name, setName] = useState("")
  const [transport, setTransport] = useState<"stdio" | "sse">("stdio")
  const [command, setCommand] = useState("")
  const [argsRaw, setArgsRaw] = useState("")
  const [envRaw, setEnvRaw] = useState("")
  const [url, setUrl] = useState("")
  const [headersRaw, setHeadersRaw] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const reset = () => {
    setName("")
    setTransport("stdio")
    setCommand("")
    setArgsRaw("")
    setEnvRaw("")
    setUrl("")
    setHeadersRaw("")
    setFormError(null)
    setSubmitting(false)
  }

  // Reset whenever the dialog closes so the next open starts fresh.
  useEffect(() => {
    if (!open) reset()
  }, [open])

  const onSubmit = async () => {
    setFormError(null)
    if (!SERVER_NAME_PATTERN.test(name)) {
      setFormError("Name must match [a-z][a-z0-9_-]{1,30} (lowercase letters/digits/_/-, 2–31 chars).")
      return
    }
    let parsedEnv: Record<string, string> | undefined
    let parsedHeaders: Record<string, string> | undefined
    try {
      parsedEnv = parseKeyValueLines(envRaw)
      parsedHeaders = parseKeyValueLines(headersRaw)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Could not parse key=value lines.")
      return
    }
    const req: McpServerAddRequest = { name, transport }
    if (transport === "stdio") {
      if (!command.trim()) { setFormError("Command is required for stdio transport."); return }
      req.command = command.trim()
      const argList = argsRaw.split("\n").map((s) => s.trim()).filter(Boolean)
      if (argList.length > 0) req.args = argList
      if (parsedEnv && Object.keys(parsedEnv).length > 0) req.env = parsedEnv
    } else {
      if (!url.trim()) { setFormError("URL is required for sse transport."); return }
      req.url = url.trim()
      if (parsedHeaders && Object.keys(parsedHeaders).length > 0) req.headers = parsedHeaders
    }

    setSubmitting(true)
    try {
      await addMcpServer(req)
      await onAdded()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Add failed.")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add external MCP server</DialogTitle>
          <DialogDescription>
            Register a new MCP server. The runtime will connect immediately and discover its tools.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 text-sm">
          <div className="grid gap-1.5">
            <Label htmlFor="mcp-add-name">Name</Label>
            <Input
              id="mcp-add-name"
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              placeholder="filesystem"
              autoComplete="off"
              spellCheck={false}
            />
            <p className="text-[11px] text-muted-foreground font-mono">[a-z][a-z0-9_-]{`{1,30}`}</p>
          </div>

          <div className="grid gap-1.5">
            <Label>Transport</Label>
            <div className="flex gap-2">
              {(["stdio", "sse"] as const).map((t) => (
                <Button
                  key={t}
                  type="button"
                  size="sm"
                  variant={transport === t ? "default" : "outline"}
                  onClick={() => setTransport(t)}
                  className="h-8"
                >
                  {t}
                </Button>
              ))}
            </div>
          </div>

          {transport === "stdio" ? (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor="mcp-add-command">Command</Label>
                <Input
                  id="mcp-add-command"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="npx"
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="mcp-add-args">Args (one per line)</Label>
                <textarea
                  id="mcp-add-args"
                  value={argsRaw}
                  onChange={(e) => setArgsRaw(e.target.value)}
                  rows={3}
                  className="rounded-md border border-input bg-transparent px-3 py-2 text-sm font-mono shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
                  placeholder={"-y\n@modelcontextprotocol/server-filesystem\n/path/to/dir"}
                  spellCheck={false}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="mcp-add-env">Environment (KEY=VALUE per line)</Label>
                <textarea
                  id="mcp-add-env"
                  value={envRaw}
                  onChange={(e) => setEnvRaw(e.target.value)}
                  rows={2}
                  className="rounded-md border border-input bg-transparent px-3 py-2 text-sm font-mono shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
                  placeholder="MY_API_KEY=…"
                  spellCheck={false}
                />
              </div>
            </>
          ) : (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor="mcp-add-url">URL</Label>
                <Input
                  id="mcp-add-url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://example.com/mcp/sse"
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="mcp-add-headers">Headers (KEY=VALUE per line)</Label>
                <textarea
                  id="mcp-add-headers"
                  value={headersRaw}
                  onChange={(e) => setHeadersRaw(e.target.value)}
                  rows={2}
                  className="rounded-md border border-input bg-transparent px-3 py-2 text-sm font-mono shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
                  placeholder="Authorization=Bearer …"
                  spellCheck={false}
                />
              </div>
            </>
          )}

          {formError && (
            <p className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" /> {formError}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void onSubmit()} disabled={submitting}>
            {submitting ? <><Loader2 className="mr-1 h-3 w-3 animate-spin" /> Adding…</> : "Add server"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function parseKeyValueLines(raw: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of raw.split("\n")) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const idx = trimmed.indexOf("=")
    if (idx <= 0) {
      throw new Error(`Bad line "${trimmed}" — expected KEY=VALUE.`)
    }
    const k = trimmed.slice(0, idx).trim()
    const v = trimmed.slice(idx + 1)
    if (!k) throw new Error(`Empty key in line "${trimmed}".`)
    out[k] = v
  }
  return out
}
