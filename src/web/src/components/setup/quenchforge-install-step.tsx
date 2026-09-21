// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useCallback, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Cpu, Copy, Check, ExternalLink, Loader2, RefreshCw } from "lucide-react"
import { logSwallowedError } from "@/lib/log-swallowed"
import { fetchHealthStatus, fetchSystemCheck } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  laneLabel,
  laneModelUnset,
  readInferenceLanes,
  type InferenceLane,
} from "@/components/monitoring/inference-lanes"
import type { SystemCheckResponse } from "@/lib/types"

interface QuenchforgeInstallStepProps {
  systemCheck: SystemCheckResponse | null
  /** Called when re-detect returns; parent updates wizard state. */
  onSystemCheckRefresh: (result: SystemCheckResponse) => void
}

const INSTALL_COMMAND = "brew install cerid-ai/tap/quenchforge"
const START_COMMAND = "brew services start quenchforge"

/** Why a quenchforge-configured slot is not usable yet, in operator words. */
function slotProblem(lane: InferenceLane): string | null {
  if (lane.provider !== "quenchforge") return null
  if (lane.degraded) {
    return `${laneLabel(lane.lane)}: falling back to ${lane.serving}${
      lane.degradedDetail ? ` — ${lane.degradedDetail}` : ""
    }`
  }
  if (laneModelUnset(lane)) {
    return `${laneLabel(lane.lane)}: no model pinned, so the slot serves whatever it loaded`
  }
  if (lane.serving === "unknown") {
    return `${laneLabel(lane.lane)}: configured but nothing has been served yet`
  }
  return null
}

/**
 * Step 2 — Quenchforge Install (skippable, conditional).
 *
 * Audit-recommended pattern: do NOT auto-shell from the web UI. Show the
 * commands, give a one-click copy button, and provide a "Re-detect" action
 * that hits ``/system-check`` again so the wizard sees a freshly installed
 * quenchforge service. The user runs the install in Terminal themselves.
 *
 * "Installed" used to mean ``ollama_detected`` — any daemon on the shared
 * wire answering /api/tags with at least one model. Neither of the two
 * commands above pulls a GGUF or pins a slot model, and nothing here probed
 * the rerank slot, so a half-configured daemon (LLM model unset, rerank
 * 503ing to the CPU fallback) showed a green badge and the wizard advanced.
 * Readiness is now per-slot, read from the /health routing snapshot.
 */
export function QuenchforgeInstallStep({
  systemCheck,
  onSystemCheckRefresh,
}: QuenchforgeInstallStepProps) {
  const [copied, setCopied] = useState<string | null>(null)
  const [redetecting, setRedetecting] = useState(false)

  const copy = useCallback((value: string, label: string) => {
    navigator.clipboard
      .writeText(value)
      .then(() => {
        setCopied(label)
        setTimeout(() => setCopied(null), 2000)
      })
      .catch((err) => {
        logSwallowedError(err, "navigator.clipboard.writeText", { label })
      })
  }, [])

  const redetect = useCallback(() => {
    setRedetecting(true)
    fetchSystemCheck()
      .then((result) => {
        onSystemCheckRefresh(result)
      })
      .catch((err) => {
        logSwallowedError(err, "fetchSystemCheck", { reason: "redetect" })
      })
      .finally(() => setRedetecting(false))
  }, [onSystemCheckRefresh])

  const { data: health } = useQuery({
    queryKey: ["health-status"],
    queryFn: fetchHealthStatus,
    refetchInterval: 15_000,
    retry: 1,
  })

  // A daemon on the shared wire answering /api/tags. Necessary, not sufficient.
  const daemonAnswering = systemCheck?.ollama_detected ?? false
  const lanes = readInferenceLanes(health?.inference_routing)
  const quenchforgeLanes = lanes.filter((l) => l.provider === "quenchforge")
  const problems = quenchforgeLanes
    .map(slotProblem)
    .filter((p): p is string => p !== null)
  const observed = quenchforgeLanes.some((l) => l.serving !== "unknown")
  const ready = daemonAnswering && observed && problems.length === 0

  const status: "ready" | "not-ready" | "unverified" | "absent" = !daemonAnswering
    ? "absent"
    : ready
      ? "ready"
      : problems.length > 0
        ? "not-ready"
        : "unverified"

  const STATUS_TEXT = {
    ready: "Ready",
    "not-ready": "Detected, not ready",
    unverified: "Detected, not verified",
    absent: "Not detected yet",
  } as const

  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Cpu className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Install Quenchforge</h3>
      <p className="mb-4 text-center text-xs text-muted-foreground">
        Run these two commands in Terminal, then click <em>Re-detect</em>.
      </p>

      <div className="space-y-3">
        <CopyBlock
          label="Install"
          value={INSTALL_COMMAND}
          copied={copied === "Install"}
          onCopy={() => copy(INSTALL_COMMAND, "Install")}
        />
        <CopyBlock
          label="Start service"
          value={START_COMMAND}
          copied={copied === "Start service"}
          onCopy={() => copy(START_COMMAND, "Start service")}
        />

        <div className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium">Status:</span>
            <Badge
              data-testid="quenchforge-status-badge"
              variant="outline"
              className={cn(
                status === "ready"
                  ? "border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400"
                  : "border-yellow-500/30 bg-yellow-500/10 text-amber-600 dark:text-amber-400",
              )}
            >
              {status === "ready" && <Check className="mr-1 h-3 w-3" />}
              {STATUS_TEXT[status]}
            </Badge>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={redetect}
            disabled={redetecting}
            className="h-7"
          >
            {redetecting ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="mr-1 h-3 w-3" />
            )}
            Re-detect
          </Button>
        </div>

        {status === "not-ready" && (
          <div
            data-testid="quenchforge-slot-problems"
            role="status"
            className="rounded-lg border border-yellow-500/30 bg-yellow-500/5 p-3 text-label-xs text-amber-700 dark:text-amber-400"
          >
            <p className="mb-1 font-medium">The daemon is up, but not every slot is usable</p>
            <ul className="list-disc space-y-0.5 pl-4">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
            <p className="mt-1.5 text-muted-foreground">
              Pin the slot models in the quenchforge LaunchAgent
              (QUENCHFORGE_DEFAULT_MODEL / _EMBED_MODEL / _RERANK_MODEL), restart
              the service, then Re-detect.
            </p>
          </div>
        )}

        {status === "unverified" && daemonAnswering && (
          <div
            data-testid="quenchforge-slot-unverified"
            role="status"
            className="rounded-lg border border-dashed border-muted-foreground/30 p-3 text-label-xs text-muted-foreground"
          >
            The daemon answered, but no inference call has run through it yet, so
            no slot has been confirmed serving.
          </div>
        )}

        <div className="rounded-lg border bg-muted/30 p-3 text-label-xs text-muted-foreground">
          <p className="mb-1 font-medium text-foreground">First-launch on macOS Sonoma+</p>
          <p>
            The first launch takes a few seconds to JIT-compile Metal shaders —
            this is normal and only happens once per model.
          </p>
          <p className="mt-2">
            If you turn on{" "}
            <code className="rounded bg-background px-1 py-0.5">QUENCHFORGE_ADVERTISE_MDNS=true</code>{" "}
            (off by default), macOS will prompt to allow local-network access.
            Approve it so Cerid can autodiscover the daemon on the local
            network. The default setup binds to{" "}
            <code className="rounded bg-background px-1 py-0.5">127.0.0.1</code>{" "}
            only and does not need mDNS.
          </p>
        </div>

        <a
          href="https://github.com/cerid-ai/quenchforge#readme"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-center gap-1 text-xs text-brand hover:underline"
        >
          Open install docs
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </>
  )
}

function CopyBlock({
  label,
  value,
  copied,
  onCopy,
}: {
  label: string
  value: string
  copied: boolean
  onCopy: () => void
}) {
  return (
    <div className="space-y-1">
      <p className="text-label-xs font-medium text-muted-foreground">{label}</p>
      <div className="flex items-center gap-2 rounded-lg border bg-card px-3 py-2">
        <code className="flex-1 truncate font-mono text-xs">{value}</code>
        <Button
          size="sm"
          variant="ghost"
          onClick={onCopy}
          className="h-7 shrink-0"
          aria-label={`Copy ${label} command`}
        >
          {copied ? (
            <Check className="h-3 w-3 text-green-600 dark:text-green-400" />
          ) : (
            <Copy className="h-3 w-3" />
          )}
        </Button>
      </div>
    </div>
  )
}
