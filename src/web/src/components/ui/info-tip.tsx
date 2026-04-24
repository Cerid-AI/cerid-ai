// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * InfoTip — hover-explainer for technical terms. Thin wrapper around the
 * existing <Tooltip> that looks up copy in `lib/glossary.ts` so the same
 * term renders the same explainer everywhere.
 *
 * Two common shapes:
 *
 *   // Inline: renders just the info icon next to an existing label
 *   <label>Injection Threshold <InfoTip term="injection-threshold" /></label>
 *
 *   // Wrap: wraps existing children, underlines on hover
 *   <InfoTip term="ndcg-at-5">NDCG@5</InfoTip>
 *
 * If a term key isn't in the glossary, InfoTip renders children unchanged
 * and (in dev) logs a warning so missing terms are caught quickly.
 */
import { Info } from "lucide-react"

import { GLOSSARY, type GlossaryKey } from "@/lib/glossary"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

interface InfoTipProps {
  term: GlossaryKey | string
  children?: React.ReactNode
  /** Tooltip placement — follows shadcn/ui Tooltip semantics. */
  side?: "top" | "right" | "bottom" | "left"
  /** Milliseconds before the tooltip appears. Defaults to the app standard (200). */
  delayDuration?: number
  /** Override the icon size (px). */
  iconSize?: number
  /** Extra className applied to the icon wrapper. */
  className?: string
}

export function InfoTip({
  term,
  children,
  side = "top",
  delayDuration = 200,
  iconSize = 14,
  className,
}: InfoTipProps) {
  const entry = GLOSSARY[term as GlossaryKey]

  if (!entry) {
    // Missing-term safety: render children as-is so the UI doesn't break,
    // and surface the gap in dev.
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console -- dev-only warning for maintainability
      console.warn(`[InfoTip] unknown glossary term: "${term}" — add it to lib/glossary.ts`)
    }
    return <>{children}</>
  }

  const tooltipBody = (
    <TooltipContent side={side} className="max-w-xs text-xs leading-relaxed">
      <p className="font-medium">{entry.label}</p>
      <p className="mt-1 text-muted-foreground dark:text-muted-foreground/90">
        {entry.short}
      </p>
      {entry.learnMoreUrl && (
        <a
          href={entry.learnMoreUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1.5 inline-block text-brand underline-offset-2 hover:underline"
        >
          Learn more →
        </a>
      )}
    </TooltipContent>
  )

  // Wrap mode: hover on children
  if (children) {
    return (
      <TooltipProvider delayDuration={delayDuration}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className={
                `cursor-help underline decoration-dotted decoration-muted-foreground/40 underline-offset-4 ${className ?? ""}`.trim()
              }
            >
              {children}
            </span>
          </TooltipTrigger>
          {tooltipBody}
        </Tooltip>
      </TooltipProvider>
    )
  }

  // Inline-icon mode: standalone info dot
  return (
    <TooltipProvider delayDuration={delayDuration}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={
              `inline-flex h-3.5 w-3.5 items-center justify-center rounded-full text-muted-foreground/80 align-middle hover:text-foreground focus:outline-none focus:ring-1 focus:ring-ring ${className ?? ""}`.trim()
            }
            aria-label={`What is ${entry.label}?`}
          >
            <Info size={iconSize} aria-hidden="true" />
          </button>
        </TooltipTrigger>
        {tooltipBody}
      </Tooltip>
    </TooltipProvider>
  )
}
