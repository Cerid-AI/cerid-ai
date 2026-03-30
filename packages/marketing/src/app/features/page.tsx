import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "Features — Cerid AI",
  description: "Full feature breakdown — RAG pipeline, verification, memory layer, Ollama integration, bulk import, and three-tier architecture.",
}

import {
  Search, Bot, Database, Shield, ShieldCheck, Brain, FileText,
  Eye, Layers, Cpu, FolderOpen, Lock, ArrowRight,
  Sparkles, Network, RefreshCw,
} from "lucide-react"
import { Card, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { BrandShield } from "@/components/brand-shield"

/* ── Feature categories with casual + technical copy ── */

const CATEGORIES = [
  {
    title: "Retrieval & RAG",
    badge: "Core",
    features: [
      {
        icon: Layers,
        title: "Unified RAG Modes",
        casual: "Three retrieval strategies that adapt to your question — manual control, smart auto-detection, or fully customizable weights.",
        technical: "Manual (pass-through), Smart (parallel KB + memory + external recall with source_breakdown), Custom Smart (Pro — per-source weights, memory type filters). Orchestrator wraps the 22-step agent_query pipeline.",
      },
      {
        icon: Search,
        title: "Hybrid Search",
        casual: "Combines keyword matching with AI-powered understanding for more accurate results than either approach alone.",
        technical: "BM25s stemmed keyword index + Snowflake Arctic v1.5 ONNX embeddings (768-dim Matryoshka). Per-chunk retrieval profiles adjust vector/keyword weights adaptively (keyword 70/30 for structured docs, vector 70/30 for prose).",
      },
      {
        icon: Cpu,
        title: "Cross-Encoder Reranking",
        casual: "Results are re-ranked by a specialized AI model that deeply compares each result to your question.",
        technical: "ms-marco-MiniLM-L-6-v2 ONNX cross-encoder. Profile-aware weights (20% CE / 80% original for keyword-strategy docs). Three modes: cross_encoder, llm_rerank, off.",
      },
      {
        icon: RefreshCw,
        title: "Adaptive Pipeline",
        casual: "The system automatically adjusts how hard it searches based on the complexity of your question.",
        technical: "8-stage pipeline: adaptive retrieval gate → query decomposition (max 4 sub-queries) → hybrid search → profile scoring → reranking → MMR diversity (lambda 0.7) → intelligent assembly → semantic cache (int8 quantized HNSW).",
      },
    ],
  },
  {
    title: "Verification",
    badge: "Core",
    features: [
      {
        icon: ShieldCheck,
        title: "Real-Time Claim Verification",
        casual: "Every AI response is automatically checked for accuracy. See inline badges showing which claims are confirmed.",
        technical: "4 claim types (factual, recency, evasion, citation). Streaming SSE with per-claim confidence. 4-level verification cascade: KB → external data sources → cross-model (GPT-4o Mini) → web search (Grok). Monte Carlo evaluation harness with 83-claim corpus.",
      },
      {
        icon: Eye,
        title: "Inline Verification UI",
        casual: "Click any footnote marker to see the source, confidence score, and reasoning behind the verification.",
        technical: "ClaimOverlay popovers with source attribution. Footnote superscripts with pointer-events-auto. Expert mode (Grok 4) for re-verification. Per-message verification selection.",
      },
    ],
  },
  {
    title: "Memory & Learning",
    badge: "Core",
    features: [
      {
        icon: Brain,
        title: "6-Type Memory Layer",
        casual: "Cerid remembers facts, decisions, preferences, project context, time-sensitive info, and conversation insights.",
        technical: "Salience formula: base_similarity × source_authority × recency_decay × access_boost × type_weight. FSRS-inspired power-law decay for decisions. Memory recall fires alongside KB query in auto-inject with 500ms timeout.",
      },
      {
        icon: Database,
        title: "Session Dedup",
        casual: "The system tracks what it's already shown you, so follow-up questions get fresh context instead of repeating old information.",
        technical: "injectedHistoryRef tracks artifact:chunk pairs per conversation session. Prior-context note tells the LLM what was shown in earlier turns. History resets on conversation change.",
      },
    ],
  },
  {
    title: "Models & Infrastructure",
    badge: "Core",
    features: [
      {
        icon: Bot,
        title: "Bring Your Own Model",
        casual: "Use any AI model from any provider. Claude, GPT, Gemini, Llama — or run a free local model.",
        technical: "OpenRouter multi-provider routing. Smart capability-based model scoring with three-way routing (manual/recommend/auto). Proactive model switch on ignorance detection.",
      },
      {
        icon: Sparkles,
        title: "Ollama Local LLM",
        casual: "Install a free local AI model with a guided wizard. 6 of 8 pipeline tasks run locally at zero cost.",
        technical: "Guided install wizard with copy-to-clipboard + auto-detect polling. host.docker.internal fallback for Docker↔native. 6/8 stages local (claim extraction, query decomposition, topic extraction, memory resolution, simple verification, reranking). Per-stage circuit breakers.",
      },
      {
        icon: Network,
        title: "Resilient Architecture",
        casual: "If any component slows down, the system gracefully adapts rather than failing completely.",
        technical: "Circuit breakers on all Bifrost + Neo4j calls. 5-tier graceful degradation (full → lite → direct → cached → offline). Shared httpx connection pool. Distributed request tracing via X-Request-ID.",
      },
    ],
  },
  {
    title: "Import & Management",
    badge: "Core",
    features: [
      {
        icon: FolderOpen,
        title: "Bulk Folder Import",
        casual: "Scan an entire folder, preview what will be imported, then confirm. Handles zip files and filters junk automatically.",
        technical: "Preview with estimation (chunks, storage). Archive extraction (zip/tar.gz). Junk filtering (DS_Store, temp files, Office locks, macOS resource forks). SSE progress streaming. Pause/resume/cancel. Batch limit 100.",
      },
      {
        icon: FileText,
        title: "Universal Parsing",
        casual: "PDFs, Word docs, Excel, emails, ebooks, plain text, code — Cerid handles them all.",
        technical: "pdfplumber with table extraction + Markdown serialization. Parsers: PDF, DOCX, XLSX, CSV, TXT, MD, EML, MBOX, EPUB, RTF. OCR/audio/vision via Pro plugins. Per-chunk retrieval profiles computed at ingest time.",
      },
      {
        icon: Lock,
        title: "Multi-KB Namespace",
        casual: "Organize knowledge into separate spaces that don't mix. Each namespace has its own search index.",
        technical: "KB_NAMESPACE env var. collection_name(domain, namespace) with backward-compatible legacy format. BM25 namespaced directory layout. ChromaDB batch writes (5000 max). BM25 LRU eviction at 8 domains.",
      },
    ],
  },
]

export default function FeaturesPage() {
  return (
    <>
      {/* Hero */}
      <section className="bg-circuit py-24 border-b divider-gold">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <BrandShield variant="vault" size={48} className="mx-auto mb-6" />
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Features
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-muted-foreground">
            Every capability, from casual use to enterprise deployment.
            Click any feature for the technical details.
          </p>
        </div>
      </section>

      {/* Feature categories */}
      {CATEGORIES.map((cat) => (
        <section key={cat.title} className="py-20 border-b border-border">
          <div className="mx-auto max-w-6xl px-6">
            <div className="flex items-center gap-3 mb-10">
              <h2 className="text-2xl font-bold tracking-tight">{cat.title}</h2>
              <Badge variant="outline" className="text-[10px] uppercase tracking-wider text-brand border-brand/30">
                {cat.badge}
              </Badge>
            </div>

            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
              {cat.features.map((f) => (
                <Card key={f.title} className="group border-border bg-card transition-all hover:border-brand/30">
                  <CardHeader className="space-y-3">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand/10 text-brand group-hover:bg-brand/20 transition-colors">
                        <f.icon className="h-5 w-5" />
                      </div>
                      <CardTitle className="text-base">{f.title}</CardTitle>
                    </div>
                    <p className="text-sm leading-relaxed text-foreground/90">{f.casual}</p>
                    <details className="group/details">
                      <summary className="cursor-pointer text-xs font-medium text-brand hover:text-brand/80 transition-colors">
                        Technical details
                      </summary>
                      <p className="mt-2 text-xs leading-relaxed text-muted-foreground font-mono">
                        {f.technical}
                      </p>
                    </details>
                  </CardHeader>
                </Card>
              ))}
            </div>
          </div>
        </section>
      ))}

      {/* Agents grid */}
      <section className="py-20 bg-muted/20 border-b divider-gold">
        <div className="mx-auto max-w-6xl px-6">
          <h2 className="text-2xl font-bold tracking-tight mb-10">10 AI Agents</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5">
            {[
              "Query", "Decomposer", "Assembler", "Triage", "Curator",
              "Rectify", "Audit", "Maintenance", "Memory", "Hallucination",
            ].map((name) => (
              <div key={name} className="rounded-lg border border-border bg-card px-3 py-2.5 text-center">
                <Bot className="mx-auto h-4 w-4 text-brand mb-1" />
                <p className="text-xs font-medium">{name}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 text-center">
        <div className="mx-auto max-w-3xl px-6">
          <h2 className="text-2xl font-bold">See it in action</h2>
          <p className="mt-3 text-muted-foreground">Clone the repo and have Cerid running in under five minutes.</p>
          <Link
            href="https://github.com/Cerid-AI/cerid-ai"
            target="_blank"
            rel="noopener noreferrer"
            className="mt-8 inline-flex h-11 items-center gap-2 rounded-lg bg-brand px-6 text-sm font-semibold text-brand-foreground shadow-lg shadow-brand/20 hover:bg-brand/90 transition-all"
          >
            Get Started
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </section>
    </>
  )
}
