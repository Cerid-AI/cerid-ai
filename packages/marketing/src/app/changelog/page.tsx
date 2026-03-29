import type { Metadata } from "next"
import { Badge } from "@/components/ui/badge"

export const metadata: Metadata = {
  title: "Changelog — Cerid AI",
  description: "Follow Cerid AI's development journey — from first RAG prototype to a mature, privacy-first AI knowledge companion.",
}

const PHASES = [
  {
    phase: 51,
    date: "Mar 2026",
    title: "Memory Salience Improvements",
    highlights: [
      "6-type memory classification (empirical, decision, preference, project_context, temporal, conversational)",
      "Per-type decay models: power-law for decisions, exponential for conversations, no decay for empirical facts",
      "Recency-weighted access counts with FSRS-inspired reinforcement",
      "Source authority weighting (user-stated > LLM-extracted > web search)",
      "Migration tooling for existing knowledge bases",
    ],
    tag: "Memory",
  },
  {
    phase: 50,
    date: "Mar 2026",
    title: "Visual Workflow Builder",
    highlights: [
      "DAG execution engine with Kahn's cycle detection",
      "SVG canvas with drag-to-reposition and type-colored nodes",
      "4 built-in workflow templates",
      "Live execution status tracking",
    ],
    tag: "Workflows",
  },
  {
    phase: "D",
    date: "Mar 2026",
    title: "Electron Desktop App",
    highlights: [
      "Native macOS + Windows packaging",
      "Docker lifecycle management via dockerode",
      "System tray integration and auto-updater",
    ],
    tag: "Desktop",
  },
  {
    phase: "A-C",
    date: "Mar 2026",
    title: "Platform Foundation",
    highlights: [
      "Unified Docker Compose with first-run setup wizard",
      "BYOK model configuration with 5 provider support",
      "Web search fallback with auto-learn ingestion",
      "A2A protocol support for agent-to-agent communication",
      "Repository architecture separation (core/app/plugins/enterprise)",
    ],
    tag: "Platform",
  },
  {
    phase: "38-39",
    date: "Mar 2026",
    title: "Performance & Security Hardening",
    highlights: [
      "MCP server optimization (60-80% verification latency reduction)",
      "Expert verification mode (Grok 4) with per-message selection",
      "Privacy hardening (CORS, port binding, encryption, audit TTL)",
      "Quantized reranker (91MB to 23MB, 3-4x faster)",
      "Production readiness audit (33 issues fixed across 4 sprints)",
    ],
    tag: "Performance",
  },
  {
    phase: 37,
    date: "Mar 2026",
    title: "UX Polish & Marketing Refresh",
    highlights: [
      "Brand identity with teal accent color system",
      "Simple / Advanced mode for progressive disclosure",
      "Settings reorganized into tabs with user experience presets",
      "First-run onboarding dialog",
      "Marketing site refreshed with Phase 32–36 features",
    ],
    tag: "UX",
  },
  {
    phase: 36,
    date: "Mar 2026",
    title: "Runtime Optimization",
    highlights: [
      "Proactive history pruning (sliding-window + LLM compression)",
      "HNSW-indexed semantic cache with O(log n) lookup",
      "Hallucination module decomposition (2,450 → 5 submodules)",
      "Async batch ingestion with watch-directory queue pattern",
    ],
    tag: "Performance",
  },
  {
    phase: 35,
    date: "Mar 2026",
    title: "Evaluation Scaffolding",
    highlights: [
      "StepTimer diagnostic timing",
      "RAGAS metrics (NDCG, MRR, P@K, R@K)",
      "Ablation study runner",
    ],
    tag: "Quality",
  },
  {
    phase: 34,
    date: "Mar 2026",
    title: "UX Audit & Advanced RAG Pipeline",
    highlights: [
      "6-stage adaptive retrieval pipeline",
      "Query decomposition with parallel sub-retrieval",
      "MMR diversity reordering + late interaction scoring",
      "Semantic query cache with quantized int8 embeddings",
      "KB admin endpoints + GUI",
    ],
    tag: "Pipeline",
  },
  {
    phase: 33,
    date: "Mar 2026",
    title: "Multi-User Auth & Marketing Site",
    highlights: [
      "Opt-in JWT authentication with tenant isolation",
      "Per-user API key encryption (Fernet)",
      "Marketing website launched at cerid.ai",
      "Usage metering dashboard",
    ],
    tag: "Auth",
  },
  {
    phase: 32,
    date: "Mar 2026",
    title: "Core Retrieval Quality Uplift",
    highlights: [
      "Cross-encoder reranker (ONNX-accelerated, 3 modes)",
      "Switchable client-side embeddings (Matryoshka, zero-migration)",
      "Contextual chunking with LLM-generated situational summaries",
    ],
    tag: "Retrieval",
  },
  {
    phase: 31,
    date: "Mar 2026",
    title: "Type Consolidation & Hook Extraction",
    highlights: [
      "BaseClaim type extraction, KBResult removed",
      "useChatSend hook extraction (ChatPanel 554 → 481 lines)",
      "Stale-closure bug fix for streaming",
    ],
    tag: "Refactor",
  },
  {
    phase: 30,
    date: "Mar 2026",
    title: "Chat Panel Decomposition",
    highlights: [
      "ChatPanel 896 → 554 lines via component extraction",
      "ChatToolbar, ChatMessages, useVerificationOrchestrator",
      "Streaming re-render optimization (500 → 5 renders)",
    ],
    tag: "Refactor",
  },
  {
    phase: 29,
    date: "Mar 2026",
    title: "Response Formatting & Verification UI",
    highlights: [
      "15 Markdown component overrides",
      "Inline claim verification with popovers and footnotes",
      "Collapsible code blocks + table of contents",
    ],
    tag: "UX",
  },
  {
    phase: 28,
    date: "Mar 2026",
    title: "Power-User Features",
    highlights: [
      "Right-click context menus on toolbar icons",
      "Drag-and-drop ingestion on KB pane and chat input",
      "Infrastructure settings + search tuning sliders",
      "Proactive model switch on ignorance detection",
    ],
    tag: "UX",
  },
  {
    phase: 27,
    date: "Mar 2026",
    title: "Infrastructure Hardening",
    highlights: [
      "LAN access with multi-interface IP detection",
      "Pre-flight validation (ports, env vars, disk space)",
      "Guided setup.sh installer",
      "Optional Caddy HTTPS gateway",
    ],
    tag: "Infrastructure",
  },
  {
    phase: 26,
    date: "Mar 2026",
    title: "Knowledge Graph & RAG Agents",
    highlights: [
      "Neo4j-backed knowledge graph with relationship extraction",
      "Circuit breakers on all Bifrost and Neo4j calls",
      "Distributed request tracing",
      "9-agent system (query, triage, curator, rectify, audit, maintenance, hallucination, memory, self-RAG)",
    ],
    tag: "Agents",
  },
]

export default function ChangelogPage() {
  return (
    <>
      <section className="bg-gradient-to-b from-background to-muted/30 py-20">
        <div className="mx-auto max-w-4xl px-6 text-center">
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Changelog
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-muted-foreground">
            A running log of what we ship. Each phase represents a focused sprint
            of improvements to retrieval, agents, UX, or infrastructure.
          </p>
        </div>
      </section>

      <section className="py-16">
        <div className="mx-auto max-w-3xl px-6">
          <div className="relative border-l border-border pl-8">
            {PHASES.map((entry, i) => (
              <div
                key={entry.phase}
                className="relative mb-10 last:mb-0"
                style={{ animationDelay: `${i * 80}ms` }}
              >
                {/* Timeline dot */}
                <div className="absolute -left-[calc(2rem+5px)] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-brand bg-background" />

                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-muted-foreground">
                    {entry.date}
                  </span>
                  <Badge variant="secondary" className="text-xs">
                    Phase {entry.phase}
                  </Badge>
                  <Badge variant="outline" className="text-xs">
                    {entry.tag}
                  </Badge>
                </div>

                <h3 className="mt-2 text-lg font-semibold">{entry.title}</h3>

                <ul className="mt-3 space-y-1.5">
                  {entry.highlights.map((hl) => (
                    <li
                      key={hl}
                      className="flex items-start gap-2 text-sm text-muted-foreground"
                    >
                      <span className="mt-1.5 block h-1 w-1 shrink-0 rounded-full bg-brand" />
                      {hl}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  )
}
