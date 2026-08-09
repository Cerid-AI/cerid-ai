// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import type React from "react"
import { useEffect, useRef, useState, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
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
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { AlertTriangle, ChevronRight, Info, Loader2, Lock, RotateCcw } from "lucide-react"
import { useNavigation } from "@/contexts/navigation-context"
import type { FeatureTier } from "@/lib/api/billing"
import { useEntitlements, type EntitlementInfo } from "@/hooks/use-entitlements"
import { defsForGroup, type SettingDef } from "@/lib/settings-registry"
import { useSettingsMode } from "@/lib/settings-mode"
import { useSettingsReveal } from "./reveal-context"
import { useIsModified, useResetSetting } from "./modified-context"
import { logSwallowedError } from "@/lib/log-swallowed"

import { Badge } from "@/components/ui/badge"

export type SectionKey = "connection" | "knowledge_ingestion" | "features" | "retrieval" | "search" | "taxonomy" | "infra_sync" | "ollama" | "kb_admin" | "credits" | "data_sources" | "rag_config" | "watched_folders" | "provider_status" | "governance_mcp" | "governance_agents" | "governance_servers" | "external_apis" | "privacy"

export const FOCUS_RING = "focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none" // drift-allowed: shadcn focus-ring convention


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
  // Stronger affordance than the prior 14-px chevron: a 16-px chevron with
  // a 150ms rotation transition (so the click visibly *does* something),
  // a slightly bolder hover background, and a left border accent on hover
  // that signals "I am a disclosure row, not a navigation link".
  return (
    <button
      type="button"
      className="mb-2 flex w-full cursor-pointer items-center gap-2 rounded-md border-l-2 border-transparent px-1 py-1 text-left transition-colors hover:border-primary/40 hover:bg-muted/60"
      onClick={onToggle}
      aria-expanded={open}
    >
      <ChevronRight
        className={cn(
          "h-4 w-4 text-muted-foreground transition-transform duration-150",
          open && "rotate-90",
        )}
        aria-hidden="true"
      />
      <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
      <h3 className="text-sm font-medium">{label}</h3>
    </button>
  )
}

/**
 * Monochrome OUTLINE tier badge — the one lock treatment (J-3: amber and
 * status colours stay reserved for verification bands). Optional `count`
 * prefixes the tier name for aggregate surfaces ("2 Pro" on a category row
 * of the settings overview).
 */
export function TierLockBadge({
  requiredTier,
  count,
}: {
  requiredTier?: FeatureTier
  count?: number
}) {
  const label = requiredTier === "enterprise" ? "Enterprise" : "Pro"
  return (
    <Badge variant="outline" className="gap-1 text-label-xs">
      <Lock className="h-2.5 w-2.5" aria-hidden="true" />
      {count !== undefined ? `${count} ${label}` : label}
    </Badge>
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

/**
 * Inline signal that a value is read-only at the UI level because it's
 * controlled by a deployment-time env var. Shows a small lock + the env
 * var name; hover reveals the canonical guidance ("Set via X in .env").
 *
 * Use anywhere a value LOOKS configurable but isn't — preventing the
 * 2026-04-23 affordance failure where Governance modes appeared togglable.
 */
export function ReadOnlyEnvHint({ envVar }: { envVar: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center gap-1 rounded-sm bg-muted/40 px-1.5 py-0.5 font-mono text-label-xs text-muted-foreground">
          <Lock className="h-2.5 w-2.5" aria-hidden="true" />
          {envVar}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-64">
        <p>Read-only here — set via <code className="font-mono">{envVar}</code> in <code className="font-mono">.env</code> and restart the MCP server.</p>
      </TooltipContent>
    </Tooltip>
  )
}

export function Row({ label, value, mono, info, readOnly }: { label: string; value: string; mono?: boolean; info?: string; readOnly?: boolean }) {
  return (
    <div className="flex cursor-default items-center justify-between">
      <span className="flex items-center gap-1">
        {info ? (
          <LabelWithInfo label={label} info={info} />
        ) : (
          <span className="text-sm text-muted-foreground">{label}</span>
        )}
        {readOnly && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Lock className="h-2.5 w-2.5 cursor-help text-muted-foreground/40" aria-label="Read-only" />
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-64">
              <p>Read-only — this is reported by the server, not a user-editable setting.</p>
            </TooltipContent>
          </Tooltip>
        )}
      </span>
      <span className={cn("text-sm", mono && "font-mono text-xs", readOnly && "text-muted-foreground")}>{value}</span>
    </div>
  )
}

export function ToggleRow({
  label,
  enabled,
  onToggle,
  info,
  disabled,
  pending,
}: {
  label: string
  enabled: boolean
  onToggle: (value: boolean) => void
  info?: string
  disabled?: boolean
  pending?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      {info ? (
        <LabelWithInfo label={label} info={info} />
      ) : (
        <span className="text-sm text-muted-foreground">{label}</span>
      )}
      <span className="flex items-center gap-1.5">
        {pending && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden="true" />}
        <Switch
          size="sm"
          aria-label={label}
          aria-busy={pending || undefined}
          checked={enabled}
          onCheckedChange={onToggle}
          disabled={disabled || pending}
        />
      </span>
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
  recommended,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step: number
  info?: string
  /** Display text like "Recommended: 400-512" below the slider */
  recommended?: string
}) {
  const display = step >= 1 ? String(value) : value.toFixed(2)
  return (
    <div className="space-y-0.5">
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-1.5">
          <LabelWithInfo
            label={`${label}: ${display}`}
            info={info ?? label}
          />
        </div>
        <Slider
          aria-label={label}
          value={[value]}
          onValueChange={([v]) => onChange(v)}
          min={min}
          max={max}
          step={step}
          className="w-32"
        />
      </div>
      {recommended && (
        <p className="text-label-xxs text-muted-foreground/80 pl-0.5">{recommended}</p>
      )}
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
          <span className="text-label-sm leading-tight text-muted-foreground">{description}</span>
        </div>
        <Switch size="sm" aria-label={label} checked={enabled} onCheckedChange={onToggle} />
      </div>
      {enabled && children && (
        <div className="ml-4 space-y-2 border-l-2 border-muted pl-3">
          {children}
        </div>
      )}
    </div>
  )
}

/**
 * Registry-driven row shell. Semantics (label, help, scope, env hint, lock
 * state, anchor) come from the `SettingDef`; layout and the control element
 * stay in JSX. Pass the control as `children`, or use `renderControl` when
 * the control needs the resolved entitlement (e.g. to disable itself).
 *
 * Subsumes `LabelWithInfo` / `InfoTip`-in-settings and all prior Pro-lock
 * treatments: locked rows render full-contrast label/help, an inert
 * control, and a monochrome OUTLINE tier badge whose popover carries the
 * "View plan" path (amber is reserved for the verification band — J-3).
 */
export function SettingRow({
  def,
  children,
  renderControl,
  className,
}: {
  def: SettingDef
  children?: ReactNode
  renderControl?: (entitlement: EntitlementInfo) => ReactNode
  className?: string
}) {
  const { forDef } = useEntitlements()
  const { goTo } = useNavigation()
  const reveal = useSettingsReveal()
  const ref = useRef<HTMLDivElement>(null)
  const [highlighted, setHighlighted] = useState(false)

  const entitlement = forDef(def)
  const locked = entitlement.state === "locked"
  const flagOff = entitlement.state === "flag-off"
  const isTarget = reveal?.id === def.id
  const modified = useIsModified(def.id)
  const reset = useResetSetting()

  useEffect(() => {
    if (!isTarget) return
    ref.current?.scrollIntoView({ block: "center" })
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (URL / navigation / reveal subscription); behavior validated in tests
    setHighlighted(true)
    const t = setTimeout(() => setHighlighted(false), 2000)
    return () => clearTimeout(t)
  }, [isTarget, reveal?.nonce])

  const control = renderControl ? renderControl(entitlement) : children

  return (
    <div
      ref={ref}
      id={def.id}
      data-setting-row={def.id}
      data-modified={modified || undefined}
      className={cn(
        "density-row scroll-mt-16 rounded-md transition-shadow",
        highlighted && "ring-2 ring-ring/50",
        modified && "border-l-2 border-brand/50 pl-2",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-0.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium">{def.label}</span>
            {locked && (
              <Popover>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    aria-label={`${def.label} requires the ${entitlement.requiredTier} plan`}
                    className={cn("inline-flex min-h-6 items-center focus-visible:rounded-sm", FOCUS_RING)}
                  >
                    <Badge variant="outline" className="gap-1 text-label-xs">
                      <Lock className="h-2.5 w-2.5" aria-hidden="true" />
                      {entitlement.requiredTier === "enterprise" ? "Enterprise" : "Pro"}
                    </Badge>
                  </button>
                </PopoverTrigger>
                <PopoverContent side="top" className="w-72 space-y-2">
                  <p className="text-sm font-medium">{def.label}</p>
                  <p className="text-sm text-muted-foreground">{def.helpText}</p>
                  <p className="text-label-sm text-muted-foreground">
                    Requires the {entitlement.requiredTier === "enterprise" ? "Enterprise" : "Pro"} plan.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => goTo("settings", { category: "plan" })}
                  >
                    View plan
                  </Button>
                </PopoverContent>
              </Popover>
            )}
            {def.writer.kind === "env" && <ReadOnlyEnvHint envVar={def.writer.envVar} />}
            <SettingInfoPopover def={def} />
            {modified && (
              <Badge variant="outline" className="gap-1 border-brand/50 text-label-xs text-brand">
                Modified
              </Badge>
            )}
            {modified && reset && (
              <button
                type="button"
                onClick={() => reset(def)}
                aria-label={`Reset ${def.label} to default`}
                className={cn(
                  "inline-flex items-center gap-1 rounded-sm px-1 py-0.5 text-label-xs text-muted-foreground hover:text-foreground",
                  FOCUS_RING,
                )}
              >
                <RotateCcw className="h-3 w-3" aria-hidden="true" />
                Reset
              </button>
            )}
          </div>
          <p className="text-label-sm leading-snug text-muted-foreground">{def.helpText}</p>
          <p className="text-label-xs text-muted-foreground/80">{def.scopeOfEffect.display}</p>
          {def.writtenBy && (
            <p className="text-label-xs text-muted-foreground/80">Also set by {def.writtenBy}</p>
          )}
          {flagOff && def.featureFlag && (
            <p className="flex items-center gap-1.5 text-label-xs text-muted-foreground">
              <span>Disabled on this server:</span>
              <ReadOnlyEnvHint envVar={def.featureFlag} />
            </p>
          )}
        </div>
        {control && (
          <div
            className={cn("shrink-0 pt-0.5", locked && "pointer-events-none opacity-50")}
            aria-disabled={locked || undefined}
          >
            {control}
          </div>
        )}
      </div>
    </div>
  )
}

function SettingInfoPopover({ def }: { def: SettingDef }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`About ${def.label}`}
          className={cn("inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground/60 hover:text-muted-foreground", FOCUS_RING)}
        >
          <Info className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" className="w-72 space-y-1.5">
        <p className="text-sm font-medium">{def.label}</p>
        <p className="text-sm text-muted-foreground">{def.helpText}</p>
        <p className="text-label-xs text-muted-foreground">{def.scopeOfEffect.display}</p>
        {(def.options ?? []).some((o) => o.helpText) && (
          <ul className="space-y-0.5 text-label-sm text-muted-foreground">
            {(def.options ?? []).map((o) =>
              o.helpText ? (
                <li key={String(o.value)}>
                  <span className="font-medium text-foreground">{o.label}</span> — {o.helpText}
                </li>
              ) : null,
            )}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  )
}

const DISCLOSURE_PREFIX = "cerid-settings-disclosure:"

function readDisclosure(category: string, group: string): boolean | null {
  try {
    const raw = localStorage.getItem(`${DISCLOSURE_PREFIX}${category}.${group}`)
    return raw === "open" ? true : raw === "closed" ? false : null
  } catch {
    return null
  }
}

function persistDisclosure(category: string, group: string, open: boolean) {
  try {
    localStorage.setItem(`${DISCLOSURE_PREFIX}${category}.${group}`, open ? "open" : "closed")
  } catch (err) {
    logSwallowedError(err, "localStorage.setItem", { key: `${DISCLOSURE_PREFIX}${category}.${group}` })
  }
}

/**
 * Per-group "Advanced — N settings" footer expander. Exactly one per group,
 * never nested. Open state is persisted per namespaced
 * `cerid-settings-disclosure:{category}.{group}` key (additive — no version
 * wipe). The default (when the user hasn't toggled the group) follows the
 * U-1 settings mode: simple ⇒ collapsed, advanced ⇒ open. Search hits and
 * `?setting=` deep links force-open regardless of mode.
 */
export function AdvancedDisclosure({
  category,
  group,
  count,
  children,
}: {
  category: string
  group: string
  /** Defaults to the number of `level: "advanced"` defs in {category}.{group}. */
  count?: number
  children: ReactNode
}) {
  const mode = useSettingsMode()
  const reveal = useSettingsReveal()
  const [override, setOverride] = useState<boolean | null>(() => readDisclosure(category, group))
  const [forcedOpen, setForcedOpen] = useState(false)

  const advancedIds = defsForGroup(category, group)
    .filter((d) => d.level === "advanced")
    .map((d) => d.id)
  const n = count ?? advancedIds.length

  const containsTarget = reveal !== null && advancedIds.includes(reveal.id)
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (URL / navigation / reveal subscription); behavior validated in tests
    if (containsTarget) setForcedOpen(true)
  }, [containsTarget, reveal?.nonce])

  const open = forcedOpen || (override ?? mode === "advanced")

  const toggle = () => {
    const next = !open
    setForcedOpen(false)
    setOverride(next)
    persistDisclosure(category, group, next)
  }

  return (
    <div data-advanced-disclosure={`${category}.${group}`}>
      <button
        type="button"
        aria-expanded={open}
        onClick={toggle}
        className="flex min-h-6 w-full items-center gap-1.5 rounded-md py-1.5 text-label-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight
          className={cn("h-3.5 w-3.5 transition-transform duration-150", open && "rotate-90")}
          aria-hidden="true"
        />
        Advanced — {n} setting{n === 1 ? "" : "s"}
      </button>
      {open && <div className="density-stack pt-1">{children}</div>}
    </div>
  )
}

/**
 * Destructive action with proportional friction. `danger="confirm"` opens
 * an AlertDialog; `danger="type-to-confirm"` additionally requires typing
 * `confirmPhrase` before the action arms (KB clear, sync import,
 * watched-folder remove, whisper delete, license deactivate).
 */
export function ConfirmActionButton({
  danger,
  title,
  description,
  actionLabel = "Confirm",
  confirmPhrase,
  onConfirm,
  disabled,
  variant = "destructive",
  size = "sm",
  className,
  children,
}: {
  danger: "confirm" | "type-to-confirm"
  title: string
  description?: string
  actionLabel?: string
  /** Required when danger="type-to-confirm". */
  confirmPhrase?: string
  onConfirm: () => void | Promise<void>
  disabled?: boolean
  variant?: React.ComponentProps<typeof Button>["variant"]
  size?: React.ComponentProps<typeof Button>["size"]
  className?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")

  const needsPhrase = danger === "type-to-confirm"
  const armed = !needsPhrase || (confirmPhrase !== undefined && typed === confirmPhrase)

  const run = async () => {
    setPending(true)
    setError("")
    try {
      await onConfirm()
      setOpen(false)
      setTyped("")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed")
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <Button
        type="button"
        variant={variant}
        size={size}
        className={className}
        disabled={disabled}
        onClick={() => {
          setTyped("")
          setError("")
          setOpen(true)
        }}
      >
        {children}
      </Button>
      <AlertDialog open={open} onOpenChange={(o) => { if (!pending) setOpen(o) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{title}</AlertDialogTitle>
            {description && <AlertDialogDescription>{description}</AlertDialogDescription>}
          </AlertDialogHeader>
          {needsPhrase && (
            <div className="space-y-1.5">
              <p className="text-sm text-muted-foreground">
                Type <code className="rounded bg-muted px-1 font-mono text-xs">{confirmPhrase}</code> to confirm.
              </p>
              <Input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                aria-label={`Type ${confirmPhrase} to confirm`}
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={!armed || pending}
              onClick={(e) => {
                e.preventDefault()
                void run()
              }}
            >
              {pending && <Loader2 className="mr-1.5 h-3 w-3 animate-spin" aria-hidden="true" />}
              {actionLabel}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

interface PairedSliderSpec {
  label: string
  value: number
  onChange: (value: number) => void
  info?: string
}

/**
 * Paired-weight mode for SliderRow (J-7): two independent sliders rendered
 * together with a live sum indicator and a warning when both weights sit
 * near zero. No normalization — the server semantics of each weight are
 * preserved (MERIDIAN's balance slider was rejected for changing them).
 */
export function SliderRowPair({
  a,
  b,
  min = 0,
  max = 1,
  step = 0.05,
  sumLabel = "Combined weight",
  nearZeroThreshold = 0.05,
  warning = "Both weights are near zero — this stage will contribute almost nothing.",
}: {
  a: PairedSliderSpec
  b: PairedSliderSpec
  min?: number
  max?: number
  step?: number
  sumLabel?: string
  nearZeroThreshold?: number
  warning?: string
}) {
  const bothNearZero = a.value <= nearZeroThreshold && b.value <= nearZeroThreshold
  return (
    <div className="space-y-2">
      <SliderRow label={a.label} value={a.value} onChange={a.onChange} min={min} max={max} step={step} info={a.info} />
      <SliderRow label={b.label} value={b.value} onChange={b.onChange} min={min} max={max} step={step} info={b.info} />
      <p className="text-label-xs text-muted-foreground tabular-nums">
        {sumLabel}: {(a.value + b.value).toFixed(2)}
      </p>
      {bothNearZero && (
        <p role="status" className="flex items-center gap-1.5 rounded bg-amber-500/10 px-2 py-1 text-label-xs text-amber-600 dark:text-amber-400">
          <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden="true" />
          {warning}
        </p>
      )}
    </div>
  )
}
