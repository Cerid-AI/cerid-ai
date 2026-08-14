// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect, useRef, useState } from "react"
import { Check, X, Minus, Loader2, MemoryStick, Container, FileText, Bot, ExternalLink, Monitor, Cpu, Zap } from "lucide-react"
import { cn } from "@/lib/utils"
import { fetchSystemCheck } from "@/lib/api"
import { SystemCheckHttpError } from "@/lib/api/settings"
import { logSwallowedError } from "@/lib/log-swallowed"
import type { SystemCheckResponse } from "@/lib/types"

// Slice of the desktop preload bridge this card uses (full shape lives in
// packages/desktop/src/preload/preload.ts). Probed feature-by-feature so a
// browser build (no window.cerid) falls back to the REST-only behavior.
interface CeridSystemCheckBridge {
  system?: {
    requirements?: () => Promise<{
      ram_gb: number
      disk_free_gb: number
      ram_sufficient: boolean
      disk_sufficient: boolean
    }>
  }
  docker?: {
    downloadUrl?: () => Promise<string>
  }
}

function getDesktopBridge(): CeridSystemCheckBridge | null {
  if (typeof window === "undefined") return null
  return (window as Window & { cerid?: CeridSystemCheckBridge }).cerid ?? null
}

// Browser-build fallback; the desktop bridge supplies the platform-correct
// URL (Apple Silicon vs Intel vs Windows/Linux) when available (RA-01).
const DOCKER_DESKTOP_FALLBACK_URL = "https://www.docker.com/products/docker-desktop/"

/** An actionable message when the server REFUSED us, or null when the failure
    was something else. Exported so the distinction is testable without a
    render. */
export function authFailureMessage(err: unknown): string | null {
  if (!(err instanceof SystemCheckHttpError)) return null
  if (err.status === 401 || err.status === 403) {
    return (
      "The server rejected this app's API key (HTTP " +
      err.status +
      "). Docker is fine — enter the server's API key above, then Connect."
    )
  }
  return null
}

interface SystemCheckCardProps {
  onCheckComplete: (result: SystemCheckResponse) => void
}

type CheckStatus = "checking" | "pass" | "warn" | "fail" | "neutral"

interface CheckItem {
  label: string
  icon: React.ComponentType<{ className?: string }>
  status: CheckStatus
  detail: string
}

// Shared by the REST result and the desktop-bridge probe so both paths
// grade RAM with the same thresholds.
function ramCheckItem(ramGb: number): CheckItem {
  const status: CheckStatus = ramGb >= 12 ? "pass" : ramGb >= 8 ? "warn" : "fail"
  const detail = ramGb >= 12
    ? `${ramGb.toFixed(0)} GB — recommended config`
    : `${ramGb.toFixed(0)} GB — lightweight mode recommended`
  return { label: "System Memory", icon: MemoryStick, status, detail }
}

function StatusIcon({ status }: { status: CheckStatus }) {
  switch (status) {
    case "checking":
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
    case "pass":
      return <Check className="h-3.5 w-3.5 text-green-600 dark:text-green-400" />
    case "warn":
      return <Minus className="h-3.5 w-3.5 text-yellow-600 dark:text-yellow-400" />
    case "fail":
      return <X className="h-3.5 w-3.5 text-destructive" />
    case "neutral":
      return <Minus className="h-3.5 w-3.5 text-muted-foreground" />
  }
}

export function SystemCheckCard({ onCheckComplete }: SystemCheckCardProps) {
  const [checks, setChecks] = useState<CheckItem[]>([
    { label: "System Memory", icon: MemoryStick, status: "checking", detail: "Detecting..." },
    { label: "Docker", icon: Container, status: "checking", detail: "Detecting..." },
    { label: "Configuration", icon: FileText, status: "checking", detail: "Detecting..." },
    { label: "Ollama", icon: Bot, status: "checking", detail: "Detecting..." },
  ])
  const [hardware, setHardware] = useState<{ os: string; cpu: string; cpuCores: number | null; gpu: string; gpuAcceleration: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [dockerMissing, setDockerMissing] = useState(false)
  const [dockerDownloadUrl, setDockerDownloadUrl] = useState(DOCKER_DESKTOP_FALLBACK_URL)
  const succeededRef = useRef(false)

  // RA-02: on desktop, RAM comes from the local bridge before (and without)
  // the REST poll — a fresh Mac with no Docker installed otherwise shows an
  // "Unknown" memory row retrying forever, because the only path to the
  // number was the server the missing Docker would have run.
  // RA-01: same mount, fetch the platform-correct Docker Desktop URL.
  useEffect(() => {
    let cancelled = false
    const bridge = getDesktopBridge()
    bridge?.system?.requirements?.()
      .then((req) => {
        if (cancelled || succeededRef.current) return
        setChecks((prev) => prev.map((c) => (c.label === "System Memory" ? ramCheckItem(req.ram_gb) : c)))
      })
      .catch((err) => logSwallowedError(err, "cerid.system.requirements"))
    bridge?.docker?.downloadUrl?.()
      .then((url) => {
        if (!cancelled && url) setDockerDownloadUrl(url)
      })
      .catch((err) => logSwallowedError(err, "cerid.docker.downloadUrl"))
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    function applyResult(result: SystemCheckResponse) {
      if (cancelled || succeededRef.current) return

      const newChecks: CheckItem[] = [
        ramCheckItem(result.ram_gb),
        {
          label: "Docker",
          icon: Container,
          status: result.docker_running ? "pass" : "fail",
          detail: result.docker_running ? "Running" : "Not detected",
        },
        {
          label: "Configuration",
          icon: FileText,
          status: result.env_exists
            ? result.env_keys_present.length > 0 ? "pass" : "neutral"
            : "neutral",
          detail: result.env_exists
            ? result.env_keys_present.length > 0
              ? `Found (${result.env_keys_present.length} key${result.env_keys_present.length === 1 ? "" : "s"} configured)`
              : "Found (no keys yet)"
            : "Fresh install",
        },
        {
          label: "Ollama",
          icon: Bot,
          status: result.ollama_detected ? "pass" : "neutral",
          detail: result.ollama_detected
            ? `Detected (${result.ollama_models.length} model${result.ollama_models.length === 1 ? "" : "s"})`
            : "Not found",
        },
      ]

      succeededRef.current = true
      setChecks(newChecks)
      setError(null)
      setRetrying(false)
      setHardware({
        os: result.os,
        cpu: result.cpu,
        cpuCores: result.cpu_cores,
        gpu: result.gpu,
        gpuAcceleration: result.gpu_acceleration,
      })
      setDockerMissing(!result.docker_running)
      onCheckComplete(result)
    }

    function tryCheck() {
      fetchSystemCheck()
        .then(applyResult)
        .catch((err: unknown) => {
          if (cancelled || succeededRef.current) return
          // A 401 is not "unreachable". Collapsing every failure into "is
          // Docker running?" sent an operator whose stack was healthy off to
          // check Docker, when the real answer was that the desktop app had no
          // API key stored — the server answered, it just said no.
          setError(authFailureMessage(err) ?? "Could not reach backend — is Docker running?")
          setRetrying(true)
          setChecks((prev) =>
            prev.map((c) => c.status === "checking"
              ? { ...c, status: "neutral" as CheckStatus, detail: "Unknown" }
              : c,
            ),
          )
          // Retry every 5s until backend responds
          retryTimer = setTimeout(tryCheck, 5000)
        })
    }

    tryCheck()

    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [onCheckComplete])

  return (
    <div className="mt-4 rounded-lg border bg-card">
      <div className="border-b px-3 py-2">
        <p className="text-xs font-medium text-muted-foreground">System Check</p>
      </div>
      <div className="divide-y">
        {checks.map((check) => {
          const Icon = check.icon
          return (
            <div key={check.label} className="flex items-center gap-3 px-3 py-2">
              <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="flex-1 text-xs font-medium">{check.label}</span>
              <span
                className={cn(
                  "text-xs",
                  check.status === "pass" && "text-green-600 dark:text-green-400",
                  check.status === "warn" && "text-yellow-600 dark:text-yellow-400",
                  check.status === "fail" && "text-destructive",
                  (check.status === "neutral" || check.status === "checking") && "text-muted-foreground",
                )}
              >
                {check.detail}
              </span>
              <StatusIcon status={check.status} />
            </div>
          )
        })}
      </div>
      {hardware && (
        <div className="border-t">
          <div className="px-3 py-1.5">
            <p className="text-label-xs font-medium uppercase tracking-wider text-muted-foreground/80">Hardware Detected</p>
          </div>
          <div className="divide-y">
            <div className="flex items-center gap-3 px-3 py-1.5">
              <Monitor className="h-3 w-3 shrink-0 text-muted-foreground/80" />
              <span className="flex-1 text-label-sm text-muted-foreground">OS</span>
              <span className="text-label-sm text-muted-foreground">{hardware.os}</span>
            </div>
            <div className="flex items-center gap-3 px-3 py-1.5">
              <Cpu className="h-3 w-3 shrink-0 text-muted-foreground/80" />
              <span className="flex-1 text-label-sm text-muted-foreground">CPU</span>
              <span className="text-label-sm text-muted-foreground">
                {hardware.cpu}{hardware.cpuCores != null ? ` (${hardware.cpuCores} cores)` : ""}
              </span>
            </div>
            <div className="flex items-center gap-3 px-3 py-1.5">
              <Zap className="h-3 w-3 shrink-0 text-muted-foreground/80" />
              <span className="flex-1 text-label-sm text-muted-foreground">GPU</span>
              <span className="text-label-sm text-muted-foreground">
                {hardware.gpu}{hardware.gpuAcceleration && hardware.gpuAcceleration !== "none" ? ` (${hardware.gpuAcceleration})` : ""}
              </span>
            </div>
          </div>
        </div>
      )}
      {error && (
        <div className="border-t px-3 py-2">
          <div className="flex items-center gap-2">
            {retrying && <Loader2 className="h-3 w-3 animate-spin shrink-0 text-muted-foreground" />}
            <p className="text-xs text-destructive">{error}</p>
          </div>
          {retrying && (
            <p className="mt-1 text-label-xs text-muted-foreground">
              Retrying automatically... Start services with{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-label-xs">./scripts/start-cerid.sh</code>
            </p>
          )}
        </div>
      )}
      {dockerMissing && (
        <div className="border-t px-3 py-2.5">
          <p className="mb-1.5 text-xs font-medium text-destructive">
            Docker is required to run Cerid AI services.
          </p>
          <ol className="ml-4 list-decimal space-y-0.5 text-label-sm text-muted-foreground">
            <li>
              <a
                href={dockerDownloadUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-brand underline hover:text-brand/80"
              >
                Download Docker Desktop
                <ExternalLink className="h-2.5 w-2.5" />
              </a>
            </li>
            <li>Install and launch Docker Desktop</li>
            <li>Return here — the check updates automatically</li>
          </ol>
        </div>
      )}
    </div>
  )
}
