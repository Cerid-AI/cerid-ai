// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { lazy, Suspense, useState, useCallback, useRef, useEffect } from "react"
import { Copy, Check, User, Bot } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

/** Lazy-load PrismLight (25 common languages, ~200KB) instead of full Prism (~1.6MB) */
const LazySyntaxHighlighter = lazy(() =>
  import("@/lib/syntax-highlighter").then((m) => ({ default: m.PrismLight })),
)

let oneDarkStyle: Record<string, React.CSSProperties> | undefined
import("@/lib/syntax-highlighter").then((m) => { oneDarkStyle = m.oneDark })
import type { ChatMessage } from "@/lib/types"
import { findModel, PROVIDER_COLORS } from "@/lib/types"
import { SourceAttribution } from "./source-attribution"

/** Code block fallback while SyntaxHighlighter loads */
function CodeFallback({ code }: { code: string }) {
  return <pre className="rounded-lg bg-[#282c34] p-4 text-sm text-gray-300 overflow-x-auto !my-0"><code>{code}</code></pre>
}

/** Module-level markdown components — avoids recreation on every render */
const MD_COMPONENTS: Record<string, React.ComponentType<Record<string, unknown>>> = {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  code({ className, children, ...props }: any) {
    const match = /language-(\w+)/.exec(className ?? "")
    const codeString = String(children).replace(/\n$/, "")

    if (match) {
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
      timerRef.current = setTimeout(() => setCopied(false), 2000)
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

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user"

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

      <div className={cn("flex max-w-[80%] flex-col gap-1", isUser && "items-end")}>
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5",
            isUser ? "bg-primary text-primary-foreground" : "bg-muted"
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : message.content === "" ? (
            <div className="flex items-center gap-1.5 py-2 px-1">
              <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: "0ms" }} />
              <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: "150ms" }} />
              <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: "300ms" }} />
            </div>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={MD_COMPONENTS}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {!isUser && message.content && (
          <div className="opacity-0 transition-opacity group-hover:opacity-100">
            <CopyButton text={message.content} />
          </div>
        )}

        {!isUser && message.sourcesUsed && message.sourcesUsed.length > 0 && message.content !== "" && (
          <SourceAttribution sources={message.sourcesUsed} />
        )}

        {message.model && <ModelBadge modelId={message.model} />}
      </div>
    </div>
  )
}

function ModelBadge({ modelId }: { modelId: string }) {
  const model = findModel(modelId)
  const label = model?.label ?? modelId.split("/").pop() ?? modelId
  const provider = model?.provider ?? ""
  const colorClass = PROVIDER_COLORS[provider] ?? "bg-muted text-muted-foreground"

  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium", colorClass)}>
      {label}
    </span>
  )
}

export { ModelBadge }