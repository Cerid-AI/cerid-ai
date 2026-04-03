// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react"
import { Check, X, Minus, Loader2, MemoryStick, Container, FileText, Bot } from "lucide-react"
import { cn } from "@/lib/utils"
import { fetchSystemCheck } from "@/lib/api"
import type { SystemCheckResponse } from "@/lib/types"

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
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    fetchSystemCheck()
      .then((result) => {
        if (cancelled) return

        const ramStatus: CheckStatus = result.ram_gb >= 12 ? "pass" : result.ram_gb >= 8 ? "warn" : "fail"
        const ramDetail = result.ram_gb >= 12
          ? `${result.ram_gb.toFixed(0)} GB — recommended config`
          : `${result.ram_gb.toFixed(0)} GB — lightweight mode recommended`

        const newChecks: CheckItem[] = [
          {
            label: "System Memory",
            icon: MemoryStick,
            status: ramStatus,
            detail: ramDetail,
          },
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

        setChecks(newChecks)
        onCheckComplete(result)
      })
      .catch(() => {
        if (cancelled) return
        setError("Could not reach backend for system check")
        setChecks((prev) =>
          prev.map((c) => ({ ...c, status: "neutral" as CheckStatus, detail: "Unknown" })),
        )
      })

    return () => { cancelled = true }
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
      {error && (
        <div className="border-t px-3 py-2">
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}
    </div>
  )
}
