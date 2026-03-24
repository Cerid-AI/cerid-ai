// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import remarkGfm from "remark-gfm"
import { lazy, Suspense, useState, useCallback, useMemo, useRef, useEffect, isValidElement, type ReactNode } from "react"

import ReactMarkdown from "react-markdown"
import { Copy, Check, User, Bot, ShieldCheck, ShieldAlert, Loader2, Pencil, Shield, ExternalLink, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn, formatCost } from "@/lib/utils"

/** Lazy-load PrismLight (25 common languages, ~200KB) instead of full Prism (~1.6MB) */
const LazySyntaxHighlighter = lazy(() =>
  import("@/lib/syntax-highlighter").then((m) => ({ default: m.PrismLight })),
)

let oneDarkStyle: Record<string, React.CSSProperties> | undefined
import("@/lib/syntax-highlighter").then((m) => { oneDarkStyle = m.oneDark })
import type { ChatMessage, HallucinationClaim } from "@/lib/types"
import { findModel, PROVIDER_COLORS } from "@/lib/types"
import { matchClaimsToText, type ClaimDisplayStatus } from "@/lib/verification-utils"
import { SourceAttribution } from "./source-attribution"
import { KBContextIndicator } from "./kb-context-indicator"
import { ClaimOverlay } from "./claim-overlay"

/** Inline styles for <mark> elements — avoids prose class specificity overriding Tailwind utilities.
 *  Verified claims use a subtle underline instead of a background highlight
 *  to keep the reading flow clean. Non-verified claims use background colors
 *  to draw attention to potential issues. */
const MARKUP_STYLES: Record<ClaimDisplayStatus, React.CSSProperties> = {
  verified: { backgroundColor: "transparent", borderBottom: "2px solid rgba(34,197,94,0.4)", textDecoration: "none", color: "inherit" },
  refuted: { backgroundColor: "rgba(239,68,68,0.15)", borderBottom: "2px solid rgba(239,68,68,0.5)", color: "inherit" },
  unverified: { backgroundColor: "rgba(234,179,8,0.12)", borderBottom: "2px solid rgba(234,179,8,0.4)", color: "inherit" },
  evasion: { backgroundColor: "rgba(249,115,22,0.12)", borderBottom: "2px solid rgba(249,115,22,0.4)", color: "inherit" },
  citation: { backgroundColor: "rgba(168,85,247,0.12)", borderBottom: "2px solid rgba(168,85,247,0.4)", color: "inherit" },
  uncertain: { backgroundColor: "rgba(107,114,128,0.1)", color: "inherit" },
  pending: { backgroundColor: "rgba(107,114,128,0.08)", color: "inherit" },
  skipped: { backgroundColor: "rgba(156,163,175,0.08)", color: "inherit" },
}

/** Code block fallback while SyntaxHighlighter loads */
function CodeFallback({ code }: { code: string }) {
  return <pre className="rounded-lg bg-[#282c34] p-4 text-sm text-gray-300 overflow-x-auto !my-0"><code>{code}</code></pre>
}

const COPY_FEEDBACK_MS = 2000
const MAX_CODE_LINES = 25

function CollapsibleCodeBlock({ language, code }: { language: string; code: string }) {
  const [expanded, setExpanded] = useState(false)
  const lines = useMemo(() => code.split("\n"), [code])
  const totalLines = lines.length
  const displayCode = expanded ? code : lines.slice(0, MAX_CODE_LINES).join("\n")

  return (
    <div className="relative my-2">
      <div className="absolute right-2 top-2 z-10 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        <CopyButton text={code} />
      </div>
      <Suspense fallback={<CodeFallback code={displayCode} />}>
        <LazySyntaxHighlighter
          style={oneDarkStyle ?? {}}
          language={language}
          PreTag="div"
          className="rounded-lg !my-0"
        >
          {displayCode}
        </LazySyntaxHighlighter>
      </Suspense>
      {!expanded && (
        <div className="absolute bottom-0 left-0 right-0 flex items-end justify-center rounded-b-lg bg-gradient-to-t from-[#282c34] pb-2 pt-8">
          <button
            className="rounded-md bg-muted/80 px-3 py-1 text-xs text-muted-foreground hover:bg-muted"
            onClick={() => setExpanded(true)}
          >
            Show all {totalLines} lines
          </button>
        </div>
      )}
      {expanded && (
        <button
          className="mt-1 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setExpanded(false)}
        >
          Collapse
        </button>
      )}
    </div>
  )
}

/** Recursively extract text content from React children (handles nested <strong>, <code>, etc.) */
function extractText(node: ReactNode): string {
  if (typeof node === "string") return node
  if (typeof node === "number") return String(node)
  if (!node) return ""
  if (Array.isArray(node)) return node.map(extractText).join("")
  if (isValidElement(node)) return extractText((node.props as { children?: ReactNode }).children)
  return ""
}

/** Generate a URL-safe slug for heading IDs; falls back to hash for non-latin text */
function slugify(text: string): string {
  const slug = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")
  return slug || `heading-${Math.random().toString(36).slice(2, 8)}`
}

interface TOCEntry {
  id: string
  text: string
  level: number
}

function MessageTOC({ headings }: { headings: TOCEntry[] }) {
  return (
    <nav className="not-prose mb-3 rounded-lg border bg-muted/20 px-3 py-2">
      <p className="mb-1 text-xs font-medium text-muted-foreground">Contents</p>
      <div className="space-y-0.5">
        {headings.map((h) => (
          <button
            key={h.id}
            className="block w-full text-left text-xs text-primary/80 hover:text-primary hover:underline"
            style={{ paddingLeft: `${(h.level - 1) * 12}px` }}
            onClick={() => {
              document.getElementById(h.id)?.scrollIntoView({ behavior: "smooth", block: "start" })
            }}
          >
            {h.text}
          </button>
        ))}
      </div>
    </nav>
  )
}

/** Module-level markdown components — avoids recreation on every render */
const MD_COMPONENTS: Record<string, React.ComponentType<Record<string, unknown>>> = {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  code({ className, children, ...props }: any) {
    const match = /language-(\w+)/.exec(className ?? "")
    const codeString = String(children).replace(/\n$/, "")

    if (match) {
      if (codeString.split("\n").length > MAX_CODE_LINES) {
        return <CollapsibleCodeBlock language={match[1]} code={codeString} />
      }
      return (
        <div className="relative my-2">
          <div className="absolute right-2 top-2 opacity-0 transition-opacity group-hover:opacity-100">
            <CopyButton text={codeString} />
          </div>
          <Suspense fallback={<CodeFallback code={codeString} />}>
            <LazySyntaxHighlighter
              style={oneDarkStyle ?? {}}
              language={match[1]}
              PreTag="div"
              className="rounded-lg !my-0"
            >
              {codeString}
            </LazySyntaxHighlighter>
          </Suspense>
        </div>
      )
    }

    return (
      <code className="rounded bg-muted px-1 py-0.5 text-sm" {...props}>
        {children}
      </code>
    )
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  a({ href, children, ...props }: any) {
    const isExternal = href?.startsWith("http")
    return (
      <a
        href={href}
        {...(isExternal && { target: "_blank", rel: "noopener noreferrer" })}
        className={cn(
          "text-primary underline decoration-primary/40 underline-offset-2 transition-colors hover:decoration-primary",
          isExternal && "inline-flex items-center gap-0.5",
        )}
        {...props}
      >
        {children}
        {isExternal && <ExternalLink className="inline h-3 w-3 shrink-0" />}
      </a>
    )
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  table({ children }: any) {
    return (
      <div className="my-2 overflow-x-auto rounded-lg border">
        <table className="min-w-full text-sm">{children}</table>
      </div>
    )
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  thead({ children }: any) {
    return <thead className="border-b bg-muted/50">{children}</thead>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tr({ children }: any) {
    return <tr className="border-b last:border-b-0 even:bg-muted/20">{children}</tr>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  th({ children }: any) {
    return <th className="px-3 py-2 text-left font-medium">{children}</th>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  td({ children }: any) {
    return <td className="px-3 py-2">{children}</td>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  blockquote({ children }: any) {
    return (
      <blockquote className="my-2 border-l-4 border-primary/30 bg-muted/30 py-1 pl-4 pr-2 text-muted-foreground [&>p]:my-1">
        {children}
      </blockquote>
    )
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  h1({ children }: any) {
    const id = slugify(extractText(children))
    return <h1 id={id} className="mb-3 mt-5 border-b border-border pb-1 text-xl font-semibold first:mt-0">{children}</h1>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  h2({ children }: any) {
    const id = slugify(extractText(children))
    return <h2 id={id} className="mb-2 mt-4 border-b border-border/50 pb-1 text-lg font-semibold first:mt-0">{children}</h2>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  h3({ children }: any) {
    const id = slugify(extractText(children))
    return <h3 id={id} className="mb-1.5 mt-3 text-base font-semibold first:mt-0">{children}</h3>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  h4({ children }: any) {
    const id = slugify(extractText(children))
    return <h4 id={id} className="mb-1 mt-2.5 text-sm font-semibold first:mt-0">{children}</h4>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ul({ children }: any) {
    return <ul className="my-1.5 list-disc space-y-0.5 pl-5 marker:text-muted-foreground">{children}</ul>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ol({ children }: any) {
    return <ol className="my-1.5 list-decimal space-y-0.5 pl-5 marker:text-muted-foreground">{children}</ol>
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  li({ children }: any) {
    return <li className="leading-relaxed">{children}</li>
  },
  hr() {
    return <hr className="my-4 border-border" />
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  img({ src, alt }: any) {
    return (
      <img
        src={src}
        alt={alt ?? ""}
        className="my-2 max-w-full rounded-lg border"
        loading="lazy"
      />
    )
  },
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [])

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), COPY_FEEDBACK_MS)
    } catch {
      // Clipboard API may fail in insecure contexts
    }
  }, [text])

  return (
    <Button variant="ghost" size="icon" className="h-6 w-6" aria-label={copied ? "Copied" : "Copy to clipboard"} onClick={handleCopy}>
      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
    </Button>
  )
}

export type MessageVerificationStatus =
  | { state: "loading" }
  | { state: "done"; verified: number; unverified: number; uncertain: number; skipped?: number; total: number; creditExhausted?: boolean; hasExpertClaims?: boolean }
  | null

function VerificationBadge({ status, onClick }: { status: MessageVerificationStatus; onClick?: () => void }) {
  if (!status) return null

  if (status.state === "loading") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
        <Loader2 className="h-2.5 w-2.5 animate-spin" />
        Verifying
      </span>
    )
  }

  const { verified, total, unverified, skipped, creditExhausted, hasExpertClaims } = status
  const accuracy = total > 0 ? Math.round((verified / total) * 100) : 0
  const hasIssues = unverified > 0
  const hasSkipped = (skipped ?? 0) > 0

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium transition-colors",
        hasExpertClaims
          ? "bg-purple-500/10 text-purple-400 hover:bg-purple-500/20"
          : creditExhausted
            ? "bg-yellow-500/10 text-yellow-400 hover:bg-yellow-500/20"
            : hasIssues
              ? "bg-red-500/10 text-red-400 hover:bg-red-500/20"
              : accuracy >= 80
                ? "bg-green-500/10 text-green-400 hover:bg-green-500/20"
                : "bg-yellow-500/10 text-yellow-400 hover:bg-yellow-500/20",
      )}
    >
      {hasExpertClaims
        ? <Sparkles className="h-2.5 w-2.5" />
        : hasIssues
          ? <ShieldAlert className="h-2.5 w-2.5" />
          : <ShieldCheck className="h-2.5 w-2.5" />
      }
      {verified}/{total} verified{hasSkipped ? ` (${skipped} skipped)` : ""}
    </button>
  )
}

interface MessageBubbleProps {
  message: ChatMessage
  verificationStatus?: MessageVerificationStatus
  verificationClaims?: HallucinationClaim[]
  inlineMarkups?: boolean
  onCorrect?: (messageId: string, correction: string) => void
  onToggleMarkup?: () => void
  /** Switch the verification panel to this message's report (non-selected messages). */
  onSelectForVerification?: () => void
  onClaimFocus?: (index: number) => void
  onArtifactClick?: (artifactId: string) => void
}

export function MessageBubble({ message, verificationStatus, verificationClaims, inlineMarkups, onCorrect, onToggleMarkup, onSelectForVerification, onClaimFocus, onArtifactClick }: MessageBubbleProps) {
  const isUser = message.role === "user"
  const [correcting, setCorrecting] = useState(false)
  const [correctionText, setCorrectionText] = useState("")
  const [headings, setHeadings] = useState<TOCEntry[]>([])
  const proseRef = useRef<HTMLDivElement>(null)
  const [proseContainer, setProseContainer] = useState<HTMLDivElement | null>(null)
  const proseCallbackRef = useCallback((node: HTMLDivElement | null) => {
    proseRef.current = node
    setProseContainer(node)
  }, [])

  // Claim span matching — runs when claims change. Reads DOM text via ref (not state)
  // to avoid re-trigger loop: DOM mutation effect → textContent changes → this effect → repeat.
  const [claimSpans, setClaimSpans] = useState<ReturnType<typeof matchClaimsToText>>([])
  const claimsMatchedRef = useRef<string>("")
  useEffect(() => {
    if (!verificationClaims?.length) { setClaimSpans([]); claimsMatchedRef.current = ""; return }
    const domText = proseRef.current?.textContent ?? undefined
    if (!domText) { setClaimSpans([]); return }
    // Guard: only recalculate if claims actually changed (not on DOM mutations)
    const claimKey = verificationClaims.map(c => c.claim).join("|")
    if (claimsMatchedRef.current === claimKey) return
    claimsMatchedRef.current = claimKey
    const spans = matchClaimsToText(message.content, verificationClaims, domText)
    if (import.meta.env.DEV && verificationClaims.length > 0 && spans.length !== verificationClaims.length) {
      console.warn(`[inline-markup] mismatch: claims=${verificationClaims.length} spans=${spans.length}`)
    }
    setClaimSpans(spans)
  }, [message.content, verificationClaims])

  // Inline verification markups via DOM text node highlighting.
  // Process spans in REVERSE order so DOM mutations (mark + footnote insertion)
  // only affect positions to the RIGHT of the current span, which have already
  // been processed.  Rebuild the text walker before each span to get fresh
  // text-node boundaries after prior mutations.
  // Uses inline styles (not Tailwind classes) so prose mark styles can't override.
  useEffect(() => {
    const container = proseRef.current
    if (!container || !inlineMarkups || !verificationClaims || verificationClaims.length === 0) return

    const spans = claimSpans
    if (spans.length === 0) return

    // Track created elements for cleanup
    const createdEls: HTMLElement[] = []

    for (let i = spans.length - 1; i >= 0; i--) {
      const span = spans[i]

      // Rebuild text nodes from fresh DOM state after each modification
      const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
      const textNodes: { node: Text; start: number; end: number }[] = []
      let offset = 0
      while (walker.nextNode()) {
        const node = walker.currentNode as Text
        const len = node.textContent?.length ?? 0
        textNodes.push({ node, start: offset, end: offset + len })
        offset += len
      }

      // Find the text node containing this span
      for (const tn of textNodes) {
        if (tn.end <= span.start || tn.start >= span.end) continue

        const localStart = Math.max(0, span.start - tn.start)
        const localEnd = Math.min(tn.node.textContent?.length ?? 0, span.end - tn.start)

        try {
          const range = document.createRange()
          range.setStart(tn.node, localStart)
          range.setEnd(tn.node, localEnd)

          const mark = document.createElement("mark")
          // Use inline styles to bypass prose mark specificity.
          // Text keeps default cursor — only the footnote [N] is clickable.
          const styleObj = MARKUP_STYLES[span.displayStatus] ?? MARKUP_STYLES.uncertain
          Object.assign(mark.style, styleObj, {
            cursor: "default",
            borderRadius: "2px",
            paddingInline: "2px",
          })
          mark.dataset.ceridClaim = "true"
          mark.dataset.claimIndex = String(i)
          range.surroundContents(mark)
          createdEls.push(mark)

          // Add footnote superscript after mark
          const sup = document.createElement("sup")
          sup.className = "ml-0.5 cursor-pointer text-[10px] font-semibold text-brand hover:text-brand/80"
          sup.textContent = `[${i + 1}]`
          sup.dataset.ceridFootnote = String(i)
          mark.parentNode?.insertBefore(sup, mark.nextSibling)
          createdEls.push(sup)
        } catch {
          // surroundContents can fail if range crosses element boundaries
        }
        break // Only mark in the first matching text node per span
      }
    }

    return () => {
      for (const el of createdEls) {
        if (el.tagName === "MARK") {
          const parent = el.parentNode
          if (parent) {
            while (el.firstChild) parent.insertBefore(el.firstChild, el)
            parent.removeChild(el)
          }
        } else {
          el.remove()
        }
      }
    }
  }, [claimSpans, verificationClaims, inlineMarkups])

  // Scan headings for TOC after render
  useEffect(() => {
    const container = proseRef.current
    if (!container || isUser) { setHeadings([]); return }
    const els = container.querySelectorAll<HTMLElement>("h1, h2, h3, h4")
    const entries: TOCEntry[] = []
    els.forEach((el) => {
      if (el.id && el.textContent) {
        entries.push({ id: el.id, text: el.textContent, level: parseInt(el.tagName[1], 10) })
      }
    })
    setHeadings(entries)
  }, [message.content, isUser])

  return (
    <div className={cn("group flex gap-3 py-4", isUser && "flex-row-reverse")}>
      <div
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted"
        )}
      >
        {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>

      <div className={cn("flex max-w-[85%] flex-col gap-1", isUser && "items-end")}>
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5",
            isUser ? "bg-primary text-primary-foreground" : "bg-muted"
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : !message.content ? (
            <div className="flex items-center gap-1.5 py-2 px-1">
              <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: "0ms" }} />
              <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: "150ms" }} />
              <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: "300ms" }} />
            </div>
          ) : (
            <div ref={proseCallbackRef} className="prose prose-sm dark:prose-invert max-w-none">
              {headings.length >= 3 && <MessageTOC headings={headings} />}
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={MD_COMPONENTS}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {!isUser && inlineMarkups && verificationClaims && verificationClaims.length > 0 && (
          <ClaimOverlay
            container={proseContainer}
            claims={verificationClaims}
            claimSpans={claimSpans}
            onClaimFocus={onClaimFocus}
            onArtifactClick={onArtifactClick}
          />
        )}

        {!isUser && !!message.content && (
          <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
            <CopyButton text={message.content} />
            {onCorrect && (
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                aria-label="Correct this response"
                onClick={() => setCorrecting(true)}
              >
                <Pencil className="h-3 w-3" />
              </Button>
            )}
          </div>
        )}

        {correcting && (
          <div className="flex items-center gap-2 rounded-lg border bg-background p-2">
            <input
              type="text"
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              placeholder="What should be corrected?"
              value={correctionText}
              onChange={(e) => setCorrectionText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && correctionText.trim()) {
                  onCorrect?.(message.id, correctionText.trim())
                  setCorrecting(false)
                  setCorrectionText("")
                }
                if (e.key === "Escape") {
                  setCorrecting(false)
                  setCorrectionText("")
                }
              }}
              autoFocus
            />
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs"
              disabled={!correctionText.trim()}
              onClick={() => {
                if (correctionText.trim()) {
                  onCorrect?.(message.id, correctionText.trim())
                  setCorrecting(false)
                  setCorrectionText("")
                }
              }}
            >
              Submit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs"
              onClick={() => { setCorrecting(false); setCorrectionText("") }}
            >
              Cancel
            </Button>
          </div>
        )}

        {!isUser && message.sourcesUsed && message.sourcesUsed.length > 0 && !!message.content && (
          <>
            <KBContextIndicator sources={message.sourcesUsed} />
            <SourceAttribution sources={message.sourcesUsed} />
          </>
        )}

        <div className="flex items-center gap-1.5">
          {message.model && <ModelBadge modelId={message.model} />}
          {!isUser && verificationStatus && <VerificationBadge status={verificationStatus} onClick={onSelectForVerification ?? onToggleMarkup} />}
          {!isUser && onToggleMarkup && verificationClaims && verificationClaims.length > 0 && (
            <Button
              variant="ghost"
              size="icon"
              className={cn("h-5 w-5", inlineMarkups ? "text-brand bg-brand/10" : "text-muted-foreground")}
              aria-label={inlineMarkups ? "Hide inline verification" : "Show inline verification"}
              onClick={onToggleMarkup}
            >
              {inlineMarkups ? <ShieldCheck className="h-3 w-3" /> : <Shield className="h-3 w-3" />}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

function ModelBadge({ modelId }: { modelId: string }) {
  const model = findModel(modelId)
  const label = model?.label ?? modelId.split("/").pop() ?? modelId
  const provider = model?.provider ?? modelId.split("/")[0]?.replace("openrouter", "OpenRouter") ?? ""
  const colorClass = PROVIDER_COLORS[provider] ?? PROVIDER_COLORS[model?.provider ?? ""] ?? "bg-muted text-muted-foreground"

  const badge = (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium", colorClass)}>
      {label}
    </span>
  )

  const caps = model?.capabilities
  const ctxK = model ? Math.round(model.contextWindow / 1000) : null
  const costIn = model ? formatCost(model.inputCostPer1M / 1000) : null
  const costOut = model ? formatCost(model.outputCostPer1M / 1000) : null

  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>{badge}</TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-xs text-xs">
          <p className="font-medium">{model?.label ?? label}</p>
          <p className="text-muted-foreground">
            {provider}{ctxK ? ` · ${ctxK}K context` : ""}
          </p>
          {caps && (
            <p className="mt-0.5 text-muted-foreground">
              Reason {caps.reasoning} · Code {caps.coding} · Creative {caps.creative} · Facts {caps.factual}
              {caps.webSearch && " · Web"}
              {caps.vision && " · Vision"}
            </p>
          )}
          {costIn && costOut && (
            <p className="mt-0.5 text-muted-foreground">
              Cost: {costIn}/1K in · {costOut}/1K out
            </p>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export { ModelBadge }