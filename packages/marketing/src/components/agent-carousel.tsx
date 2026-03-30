"use client"

import { useState, useEffect } from "react"
import { Bot, ChevronLeft, ChevronRight } from "lucide-react"

const AGENTS = [
  { name: "Query", desc: "Orchestrates multi-domain KB search with hybrid retrieval, reranking, and context assembly." },
  { name: "Decomposer", desc: "Breaks complex questions into parallel sub-queries for comprehensive coverage." },
  { name: "Assembler", desc: "Intelligently assembles context from diverse sources with facet coverage and token budgeting." },
  { name: "Triage", desc: "Routes incoming files through the ingestion pipeline — parse, classify, chunk, store." },
  { name: "Curator", desc: "Audits knowledge quality, recommends improvements, and scores artifacts." },
  { name: "Rectify", desc: "Detects duplicates, stale content, orphaned chunks, and auto-fixes integrity issues." },
  { name: "Audit", desc: "Tracks costs, latency, query patterns, and generates usage analytics reports." },
  { name: "Maintenance", desc: "Runs scheduled health checks, cleanup, and index optimization in the background." },
  { name: "Memory", desc: "Extracts facts, decisions, and preferences from conversations with conflict resolution." },
  { name: "Verification", desc: "Validates every AI claim against KB, external sources, and cross-model checks." },
]

export function AgentCarousel() {
  const [current, setCurrent] = useState(0)

  // Auto-rotate every 4 seconds
  useEffect(() => {
    const timer = setInterval(() => {
      setCurrent((c) => (c + 1) % AGENTS.length)
    }, 4000)
    return () => clearInterval(timer)
  }, [])

  const prev = () => setCurrent((c) => (c - 1 + AGENTS.length) % AGENTS.length)
  const next = () => setCurrent((c) => (c + 1) % AGENTS.length)

  const agent = AGENTS[current]

  return (
    <div className="w-full">
      <h2 className="text-2xl font-bold tracking-tight mb-6">10 AI Agents</h2>

      {/* Main card */}
      <div className="relative overflow-hidden rounded-xl border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border/50 px-5 py-3">
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-brand" />
            <span className="text-sm font-semibold">{agent.name} Agent</span>
          </div>
          <span className="text-xs text-muted-foreground">{current + 1} / {AGENTS.length}</span>
        </div>
        <div className="px-5 py-4 min-h-[80px]">
          <p className="text-sm leading-relaxed text-muted-foreground">{agent.desc}</p>
        </div>

        {/* Navigation */}
        <div className="flex items-center justify-between border-t border-border/50 px-3 py-2">
          <button onClick={prev} className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors" aria-label="Previous agent">
            <ChevronLeft className="h-4 w-4" />
          </button>
          {/* Dot indicators */}
          <div className="flex gap-1">
            {AGENTS.map((_, i) => (
              <button
                key={i}
                onClick={() => setCurrent(i)}
                className={`h-1.5 rounded-full transition-all ${i === current ? "w-4 bg-brand" : "w-1.5 bg-muted-foreground/30"}`}
                aria-label={`Go to agent ${i + 1}`}
              />
            ))}
          </div>
          <button onClick={next} className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors" aria-label="Next agent">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
