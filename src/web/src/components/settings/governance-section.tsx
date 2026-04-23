// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react"
import { ShieldAlert, ShieldCheck, ShieldX, Server, Lock, Copy, Check, AlertTriangle } from "lucide-react"
import type { ServerSettings } from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { fetchMcpServers, type McpServerInfo } from "@/lib/api/governance"
import { logSwallowedError } from "@/lib/log-swallowed"
import { SectionHeading, LabelWithInfo, Row } from "./settings-primitives"
import type { SectionKey } from "./settings-primitives"

interface GovernanceSectionProps {
  settings: ServerSettings
  sections: Record<SectionKey, boolean>
  toggleSection: (key: SectionKey) => void
}

/**
 * Sprint 1 governance surfaces (MCP_CLIENT_MODE, STRICT_AGENTS_ONLY,
 * external MCP server list).
 *
 * Read-only — these are deployment-time env-var controls. The UI shows
 * the current state plus a copy-to-clipboard for the env line operators
 * paste into `.env`. Add/remove of external MCP servers via REST is a
 * Sprint 2 polish item; this iteration only displays the configured set.
 */
export function GovernanceSection({ settings, sections, toggleSection }: GovernanceSectionProps) {
  const mode = settings.mcp_client_mode ?? "permissive"
  const allowlist = settings.mcp_client_allowlist ?? []
  const strictAgents = settings.strict_agents_only ?? false

  const [servers, setServers] = useState<McpServerInfo[]>([])
  const [serversError, setServersError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchMcpServers()
      .then((res) => { if (!cancelled) setServers(res.servers) })
      .catch((err) => {
        if (!cancelled) setServersError(err instanceof Error ? err.message : "fetch failed")
        logSwallowedError(err, "governance.fetchMcpServers")
      })
    return () => { cancelled = true }
  }, [])

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
            {serversError ? (
              <p className="text-xs text-yellow-600 dark:text-yellow-400 flex items-center gap-1">
                <AlertTriangle className="h-3 w-3" /> Could not load /mcp-servers: {serversError}
              </p>
            ) : servers.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No external MCP servers registered. Add via <code className="font-mono">POST /mcp-servers</code> or set <code className="font-mono">MCP_SERVERS_CONFIG</code> in your .env (JSON array).
              </p>
            ) : (
              <div className="grid gap-2">
                {servers.map((s) => (
                  <ServerRow key={s.name} server={s} mode={mode} allowlist={allowlist} />
                ))}
                <Row label="Total servers" value={String(servers.length)} info="Configured external MCP servers" />
                <Row
                  label="Total external tools"
                  value={String(servers.reduce((acc, s) => acc + (s.tool_count ?? 0), 0))}
                  info="Total ext_* tools available to agents from connected servers"
                />
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              Add / remove via <code className="font-mono">/mcp-servers</code> REST endpoints. UI CRUD ships in Sprint 2.
            </p>
          </CardContent>
        </Card>
      )}
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

function ServerRow({ server, mode, allowlist }: { server: McpServerInfo; mode: string; allowlist: string[] }) {
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
        </div>
      </div>
      {server.error && (
        <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">{server.error}</p>
      )}
      <div className="mt-1 flex items-center gap-2 text-muted-foreground">
        <span>{server.tool_count} tool{server.tool_count !== 1 ? "s" : ""}</span>
        {server.tools && server.tools.length > 0 && (
          <span className="truncate font-mono">{server.tools.slice(0, 4).join(", ")}{server.tools.length > 4 ? "…" : ""}</span>
        )}
      </div>
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
