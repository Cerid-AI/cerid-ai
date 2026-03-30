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
import { AgentCarousel } from "@/components/agent-carousel"

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
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-12 md:grid-cols-2">
            <div>
              <div className="gold-line w-16 mb-6" />
              <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
                Features
              </h1>
              <p className="mt-4 max-w-lg text-lg text-muted-foreground">
                Every capability, from casual use to enterprise deployment.
                Click any feature card for technical details.
              </p>
            </div>
            <div className="flex justify-center">
              <img src="/hero-tagline.jpg" alt="Cerid — Your Private AI Knowledge Companion" className="w-full max-w-lg rounded-xl border border-border/30 shadow-xl" />
            </div>
          </div>
        </div>
      </section>

      {/* Feature categories */}
      {CATEGORIES.map((cat) => (
        <section key={cat.title} className="py-12 border-b border-border">
          <div className="mx-auto max-w-6xl px-6">
            <div className="flex items-center gap-3 mb-6">
              <h2 className="text-2xl font-bold tracking-tight">{cat.title}</h2>
              <Badge variant="outline" className="text-[10px] uppercase tracking-wider text-brand border-brand/30">
                {cat.badge}
              </Badge>
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {cat.features.map((f) => (
                <details key={f.title} className="group/card">
                  <summary className="cursor-pointer list-none">
                    <Card className="border-border bg-card transition-all hover:border-brand/30 hover:shadow-md hover:shadow-brand/5">
                      <CardHeader className="space-y-2 p-4">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2.5">
                            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand/10 text-brand">
                              <f.icon className="h-4 w-4" />
                            </div>
                            <CardTitle className="text-sm">{f.title}</CardTitle>
                          </div>
                          <svg className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform group-open/card:rotate-180" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" /></svg>
                        </div>
                        <p className="text-xs leading-relaxed text-muted-foreground">{f.casual}</p>
                      </CardHeader>
                    </Card>
                  </summary>
                  <div className="mx-4 mb-3 rounded-b-lg border border-t-0 border-border bg-muted/30 px-4 py-3">
                    <p className="text-[11px] leading-relaxed text-muted-foreground/80 font-mono">
                      {f.technical}
                    </p>
                  </div>
                </details>
              ))}
            </div>
          </div>
        </section>
      ))}

      {/* Agents carousel */}
      <section className="py-20 bg-circuit border-b divider-gold">
        <div className="mx-auto max-w-4xl px-6">
          <AgentCarousel />
        </div>
      </section>

      {/* CTA with architecture graphic */}
      <section className="py-20 bg-circuit">
        <div className="mx-auto max-w-5xl px-6">
          <div className="grid grid-cols-1 items-center gap-12 md:grid-cols-2">
            <div className="flex justify-center">
              <img src="/architecture.jpg" alt="Extensible Architecture" className="w-full max-w-sm rounded-xl border border-border/30 shadow-xl" loading="lazy" />
            </div>
            <div className="text-center md:text-left">
              <div className="gold-line w-16 mb-4 mx-auto md:mx-0" />
              <h2 className="text-2xl font-bold">See it in action</h2>
              <p className="mt-3 text-muted-foreground">
                Clone the repo and have Cerid running in under five minutes.
                10 agents, 21 core tools, 32 API routers — all yours.
              </p>
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
          </div>
        </div>
      </section>
    </>
  )
}
