// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type React from "react"
import { cn } from "@/lib/utils"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { ChevronDown, ChevronRight, Info } from "lucide-react"

import { Badge } from "@/components/ui/badge"

export type SectionKey = "connection" | "knowledge_ingestion" | "features" | "retrieval" | "search" | "taxonomy" | "infra_sync" | "ollama" | "kb_admin" | "credits" | "data_sources" | "rag_config" | "watched_folders"

/**
 * Wraps children with a disabled overlay + Pro badge when the current tier
 * is community. On Pro/Enterprise the children render normally.
 */
export function ProGate({ tier, children }: { tier: string; children: React.ReactNode }) {
  if (tier !== "community") return <>{children}</>
  return (
    <div className="relative">
      <div className="opacity-40 pointer-events-none select-none">{children}</div>
      <Badge variant="outline" className="absolute right-0 top-0 text-[10px] px-1.5 py-0 text-gold border-gold">
        Pro
      </Badge>
    </div>
  )
}

export function SectionHeading({
  icon: Icon,
  label,
  open,
  onToggle,
}: {
  icon: typeof Info
  label: string
  open: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      className="mb-2 flex w-full cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-left hover:bg-muted/50"
      onClick={onToggle}
      aria-expanded={open}
    >
      {open ? (
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      ) : (
        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
      )}
      <Icon className="h-4 w-4 text-muted-foreground" />
      <h3 className="text-sm font-medium">{label}</h3>
    </button>
  )
}

export function InfoTip({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Info className="h-3.5 w-3.5 shrink-0 cursor-help text-muted-foreground/50 hover:text-muted-foreground" />
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-64">
        <p>{text}</p>
      </TooltipContent>
    </Tooltip>
  )
}

export function LabelWithInfo({ label, info }: { label: string; info: string }) {
  return (
    <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
      {label}
      <InfoTip text={info} />
    </span>
  )
}

export function Row({ label, value, mono, info }: { label: string; value: string; mono?: boolean; info?: string }) {
  return (
    <div className="flex items-center justify-between">
      {info ? (
        <LabelWithInfo label={label} info={info} />
      ) : (
        <span className="text-sm text-muted-foreground">{label}</span>
      )}
      <span className={cn("text-sm", mono && "font-mono text-xs")}>{value}</span>
    </div>
  )
}

export function ToggleRow({
  label,
  enabled,
  onToggle,
  info,
}: {
  label: string
  enabled: boolean
  onToggle: (value: boolean) => void
  info?: string
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      {info ? (
        <LabelWithInfo label={label} info={info} />
      ) : (
        <span className="text-sm text-muted-foreground">{label}</span>
      )}
      <Switch size="sm" checked={enabled} onCheckedChange={onToggle} />
    </div>
  )
}

export function SliderRow({
  label,
  value,
  onChange,
  min,
  max,
  step,
  info,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step: number
  info?: string
}) {
  const display = step >= 1 ? String(value) : value.toFixed(2)
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="flex min-w-0 items-center gap-1.5">
        <LabelWithInfo
          label={`${label}: ${display}`}
          info={info ?? label}
        />
      </div>
      <Slider
        value={[value]}
        onValueChange={([v]) => onChange(v)}
        min={min}
        max={max}
        step={step}
        className="w-32"
        aria-label={label}
      />
    </div>
  )
}

export function PipelineToggle({
  label,
  enabled,
  onToggle,
  description,
  info,
  children,
}: {
  label: string
  enabled: boolean
  onToggle: (value: boolean) => void
  description: string
  info?: string
  children?: React.ReactNode
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <span className="flex items-center gap-1.5 text-sm font-medium">
            {label}
            {info && <InfoTip text={info} />}
          </span>
          <span className="text-[11px] leading-tight text-muted-foreground">{description}</span>
        </div>
        <Switch size="sm" checked={enabled} onCheckedChange={onToggle} />
      </div>
      {enabled && children && (
        <div className="ml-4 space-y-2 border-l-2 border-muted pl-3">
          {children}
        </div>
      )}
    </div>
  )
}
