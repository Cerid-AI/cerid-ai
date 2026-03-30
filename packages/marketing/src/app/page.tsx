import Link from "next/link"
import {
  Brain,
  Lock,
  Search,
  ShieldCheck,
  Sparkles,
  Bot,
  Layers,
  ArrowRight,
  FolderOpen,
  Eye,
  CheckCircle,
  Cpu,
  Zap,
  BookOpen,
  Briefcase,
  Code2,
  Terminal,
} from "lucide-react"
import { BrandShield } from "@/components/brand-shield"
import { ExpandableCard } from "@/components/expandable-card"

/* ── Features with expandable detail ── */
const FEATURES = [
  {
    iconName: "Layers",
    title: "Smart Retrieval",
    summary: "Ask anything — Cerid searches all your files and finds the best answer, adapting its strategy to each document type.",
    detail: "Three RAG modes (Manual, Smart, Custom Smart) with per-chunk retrieval profiles. Hybrid BM25 + vector search. Cross-encoder reranking. Adaptive pipeline adjusts search depth based on question complexity.",
    link: { href: "/features#retrieval", label: "See pipeline details" },
    image: "/badge-rag.jpg",
  },
  {
    iconName: "ShieldCheck",
    title: "Verified Answers",
    summary: "Every response is fact-checked in real time. Inline badges show what's confirmed, uncertain, or needs review.",
    detail: "4-type claim detection (factual, recency, evasion, citation). Streaming SSE verification. Click any footnote to see source, confidence, and reasoning. Expert mode uses frontier models for re-verification.",
    link: { href: "/features#verification", label: "See verification flow" },
    image: "/badge-verification.jpg",
  },
  {
    iconName: "Bot",
    title: "Any AI Model",
    summary: "Claude, GPT, Gemini, Llama — or run a free local model with Ollama. Zero lock-in, zero mandatory API costs.",
    detail: "Smart capability-based routing across OpenRouter providers. Guided Ollama install wizard. 6 of 8 pipeline tasks run locally at $0. Proactive model switching on ignorance detection.",
    link: { href: "/features#models", label: "See model routing" },
    image: "/badge-byom.jpg",
  },
  {
    iconName: "Brain",
    title: "Learns From You",
    summary: "Cerid remembers your preferences, decisions, and key facts. Each conversation makes the system smarter.",
    detail: "6-type memory salience scoring: empirical facts, decisions, preferences, project context, temporal events, conversational insights. Memory recall auto-injects alongside KB context.",
    image: "/badge-agents.jpg",
  },
  {
    iconName: "Lock",
    title: "Totally Private",
    summary: "Your data never leaves your computer. No cloud. No telemetry. Only the query context you choose goes to the LLM.",
    detail: "Self-hosted Docker stack with ChromaDB, Neo4j, Redis — all on your machine. Optional Fernet encryption at rest. Open source Apache-2.0.",
    image: "/badge-secure.jpg",
  },
  {
    iconName: "FolderOpen",
    title: "Easy Import",
    summary: "Point at a folder — Cerid scans, previews, and indexes everything. PDFs, docs, code, notes, even zip archives.",
    detail: "Bulk folder scan with estimation preview. Archive extraction (zip/tar). Automatic junk filtering. SSE progress streaming with pause/resume controls. 30+ file types supported.",
    image: "/badge-architecture.jpg",
  },
]

/* ── Personas ── */
const PERSONAS = [
  {
    icon: BookOpen,
    title: "Researchers",
    highlight: "Search across thousands of papers and get verified answers in seconds.",
    bullets: ["Cross-reference findings across studies", "Fact-check AI claims against your sources", "Build a persistent knowledge graph"],
  },
  {
    icon: Briefcase,
    title: "Analysts",
    highlight: "Connect reports, financial data, and memos into one searchable brain.",
    bullets: ["Adaptive scoring for structured documents", "Tax returns, spreadsheets, and PDFs understood deeply", "Memory layer tracks decisions and project context"],
  },
  {
    icon: Code2,
    title: "Developers",
    highlight: "Self-host with Docker. Extend with MCP tools. Full API access.",
    bullets: ["26 MCP tools, 10 AI agents, 32 API routers", "Plugin system with BSL-1.1 commercial tier", "Ollama local LLM for $0 pipeline costs"],
  },
]

export default function Home() {
  return (
    <>
      {/* ══════════════ HERO ══════════════ */}
      <section className="relative overflow-hidden py-20 md:py-36 bg-circuit">
        <div className="pointer-events-none absolute inset-0 bg-hero-glow" />

        <div className="relative mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-16 md:grid-cols-2">
            <div className="text-center md:text-left">
              <div className="inline-flex items-center gap-2 rounded-full border border-brand/20 bg-brand/5 px-4 py-1.5 text-sm text-brand">
                <Sparkles className="h-3.5 w-3.5" />
                Open source &middot; Self-hosted &middot; Privacy-first
              </div>

              <h1 className="mt-8 text-4xl font-bold tracking-tight leading-[1.08] sm:text-5xl lg:text-6xl">
                Your Private AI
                <br />
                <span className="text-brand-gradient">Knowledge Companion</span>
              </h1>

              <p className="mt-6 max-w-lg text-lg leading-relaxed text-muted-foreground md:text-xl">
                Turn your files, notes, and documents into a searchable AI assistant
                that verifies its own answers. Everything stays on your machine.
              </p>

              <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row md:justify-start">
                <Link
                  href="https://github.com/Cerid-AI/cerid-ai"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-12 items-center gap-2 rounded-lg bg-brand px-7 text-sm font-semibold text-brand-foreground shadow-lg shadow-brand/20 transition-all hover:bg-brand/90 hover:shadow-brand/30"
                >
                  Get Started Free
                  <ArrowRight className="h-4 w-4" />
                </Link>
                <Link href="/features" className="inline-flex h-12 items-center rounded-lg border border-border px-7 text-sm font-medium transition-colors hover:bg-accent">
                  Explore Features
                </Link>
              </div>

              {/* Tier pills */}
              <div className="mt-8 flex flex-wrap items-center gap-2.5 md:justify-start justify-center">
                <span className="rounded-full border border-brand/40 bg-brand/10 px-3 py-1 text-[11px] font-semibold tracking-wide text-brand uppercase">Core — Free</span>
                <span className="rounded-full border border-border bg-muted/50 px-3 py-1 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">Pro</span>
                <span className="rounded-full border border-gold bg-gold/10 px-3 py-1 text-[11px] font-semibold tracking-wide text-gold uppercase">Vault — Enterprise</span>
              </div>
            </div>

            {/* Hero image with glow */}
            <div className="flex justify-center">
              <div className="glow-teal rounded-2xl">
                <img src="/hero-shield.jpg" alt="Cerid — gold shield with glowing teal C" className="float w-64 rounded-2xl md:w-72 lg:w-80" />
              </div>
            </div>
          </div>

          {/* Consumer-focused stats */}
          <div className="mx-auto mt-16 max-w-xl">
            <div className="gold-line mx-auto w-20 mb-10" />
            <div className="flex flex-wrap items-center justify-center gap-10 text-center">
              {[
                { value: "100%", label: "Private" },
                { value: "30+", label: "File Types" },
                { value: "5 min", label: "Setup" },
                { value: "Free", label: "Open Source" },
              ].map((s) => (
                <div key={s.label} className="min-w-[70px]">
                  <p className="text-2xl font-bold text-brand text-glow-brand">{s.value}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{s.label}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ══════════════ HOW IT WORKS (moved up) ══════════════ */}
      <section className="py-20 border-t divider-gold">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <div className="gold-line mx-auto w-16 mb-6" />
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">How it works</h2>
            <p className="mt-4 text-muted-foreground">One command to start. Ask a question. Get a verified answer.</p>
          </div>

          <div className="mt-12 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
            {[
              { n: "1", icon: FolderOpen, title: "Import your files", desc: "Drop a folder or upload files. Cerid parses PDFs, docs, code, emails, and archives automatically." },
              { n: "2", icon: Search, title: "Ask anything", desc: "Natural language queries search all your knowledge. Adaptive retrieval matches the right strategy." },
              { n: "3", icon: Eye, title: "See the evidence", desc: "Inline verification badges on every claim. Click any footnote to see the source and reasoning." },
              { n: "4", icon: Brain, title: "Cerid learns", desc: "Facts, decisions, and preferences are remembered. Each conversation makes the system smarter." },
            ].map((s) => (
              <div key={s.n} className="flex flex-col bg-card p-7">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-brand/15 text-sm font-bold text-brand text-glow-brand">{s.n}</span>
                  <s.icon className="h-5 w-5 text-muted-foreground" />
                </div>
                <h3 className="mt-5 text-base font-semibold">{s.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══════════════ FEATURES (expandable cards) ══════════════ */}
      <section className="py-20 bg-circuit">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <div className="gold-line mx-auto w-16 mb-6" />
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">Everything you need</h2>
            <p className="mx-auto mt-4 max-w-xl text-lg text-muted-foreground">
              Tap any card to learn more. Powerful enough for enterprise intelligence.
              Simple enough to set up in five minutes.
            </p>
          </div>

          <div className="mt-12 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <ExpandableCard key={f.title} iconName={f.iconName} title={f.title} summary={f.summary} detail={f.detail} link={f.link} image={f.image} />
            ))}
          </div>

          <div className="mt-10 text-center">
            <Link href="/features" className="inline-flex items-center gap-1.5 text-sm font-medium text-brand hover:text-brand/80 transition-colors">
              View all features with technical details <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>
      </section>

      {/* ══════════════ PRIVACY ══════════════ */}
      <section className="py-20 border-t divider-gold bg-muted/20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-16 md:grid-cols-2">
            <div>
              <div className="gold-line w-16 mb-6" />
              <h2 className="text-3xl font-bold tracking-tight">Privacy by design</h2>
              <p className="mt-4 text-lg text-muted-foreground">
                Your knowledge base stays on your machine. Cerid never phones home.
              </p>
              <ul className="mt-8 space-y-5">
                {[
                  { icon: Lock, text: "All data stored locally — nothing leaves your machine" },
                  { icon: ShieldCheck, text: "Encrypted at rest for sensitive knowledge bases" },
                  { icon: Cpu, text: "Free local AI option — no API costs required" },
                  { icon: CheckCircle, text: "Open source — audit every line of code" },
                  { icon: Zap, text: "Smart model routing — always the right model for the task" },
                ].map((item) => (
                  <li key={item.text} className="flex items-start gap-3">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand/10">
                      <item.icon className="h-4 w-4 text-brand" />
                    </div>
                    <span className="text-sm leading-relaxed text-muted-foreground">{item.text}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex justify-center">
              <img src="/privacy.jpg" alt="Privacy-First" className="float w-full max-w-md rounded-xl border border-border/30 shadow-xl" loading="lazy" />
            </div>
          </div>
        </div>
      </section>

      {/* ══════════════ WHO IT'S FOR (personas) ══════════════ */}
      <section className="py-20 bg-circuit">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <div className="gold-line mx-auto w-16 mb-6" />
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">Built for you</h2>
            <p className="mt-4 text-muted-foreground">Whatever your field, Cerid adapts to how you work.</p>
          </div>

          <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-3">
            {PERSONAS.map((p) => (
              <div key={p.title} className="rounded-xl border border-border bg-card p-7 transition-all hover:border-brand/30 hover:shadow-lg hover:shadow-brand/5">
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand/10 text-brand mb-4">
                  <p.icon className="h-5 w-5" />
                </div>
                <h3 className="text-lg font-semibold">{p.title}</h3>
                <p className="mt-2 text-sm font-medium text-brand">{p.highlight}</p>
                <ul className="mt-4 space-y-2">
                  {p.bullets.map((b) => (
                    <li key={b} className="flex items-start gap-2 text-xs text-muted-foreground">
                      <CheckCircle className="mt-0.5 h-3 w-3 shrink-0 text-brand/60" />
                      {b}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══════════════ UNDER THE HOOD (developer section) ══════════════ */}
      <section className="py-20 border-t divider-gold bg-muted/20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-16 md:grid-cols-2">
            <div>
              <div className="gold-line w-16 mb-6" />
              <h2 className="text-3xl font-bold tracking-tight">Under the hood</h2>
              <p className="mt-4 text-muted-foreground">For engineers who want to understand the architecture.</p>
              <div className="mt-8 grid grid-cols-2 gap-4">
                {[
                  { value: "10", label: "AI Agents" },
                  { value: "26", label: "MCP Tools" },
                  { value: "32", label: "API Routers" },
                  { value: "2,311+", label: "Tests" },
                  { value: "9/9", label: "CI Green" },
                  { value: "6/8", label: "Local Pipeline" },
                ].map((s) => (
                  <div key={s.label} className="rounded-lg border border-border bg-card px-4 py-3">
                    <p className="text-lg font-bold text-brand text-glow-brand">{s.value}</p>
                    <p className="text-xs text-muted-foreground">{s.label}</p>
                  </div>
                ))}
              </div>
            </div>
            <div className="flex flex-col items-center gap-4">
              <img src="/architecture.jpg" alt="Extensible Architecture" className="w-full max-w-sm rounded-xl border border-border/30 shadow-xl" loading="lazy" />
              <img src="/badge-orchestrator.jpg" alt="Local Orchestrator" className="w-32 rounded-lg border border-border/30" loading="lazy" />
            </div>
          </div>
        </div>
      </section>

      {/* ══════════════ CTA ══════════════ */}
      <section className="py-20 bg-circuit">
        <div className="mx-auto max-w-3xl px-6 text-center">
          <BrandShield variant="vault" size={56} animate className="mx-auto mb-8" />
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">Start in 5 minutes</h2>
          <p className="mx-auto mt-4 max-w-xl text-lg text-muted-foreground">
            Clone the repo, run the setup script, and your private AI companion is ready.
          </p>

          {/* Terminal command */}
          <div className="mx-auto mt-8 max-w-lg overflow-hidden rounded-lg border border-border bg-card">
            <div className="flex items-center gap-1.5 border-b border-border px-4 py-2">
              <span className="h-2.5 w-2.5 rounded-full bg-red-500/60" />
              <span className="h-2.5 w-2.5 rounded-full bg-yellow-500/60" />
              <span className="h-2.5 w-2.5 rounded-full bg-green-500/60" />
              <span className="ml-2 text-[10px] text-muted-foreground">Terminal</span>
            </div>
            <div className="px-4 py-3 text-left font-mono text-sm text-muted-foreground">
              <span className="text-brand">$</span> git clone https://github.com/Cerid-AI/cerid-ai.git
              <br />
              <span className="text-brand">$</span> cd cerid-ai && ./scripts/start-cerid.sh
            </div>
          </div>

          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              href="https://github.com/Cerid-AI/cerid-ai"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-12 items-center gap-2 rounded-lg bg-brand px-7 text-sm font-semibold text-brand-foreground shadow-lg shadow-brand/20 transition-all hover:bg-brand/90"
            >
              View on GitHub <ArrowRight className="h-4 w-4" />
            </Link>
            <Link href="/pricing" className="inline-flex h-12 items-center rounded-lg border border-border px-7 text-sm font-medium transition-colors hover:bg-accent">
              Compare Plans
            </Link>
          </div>
        </div>
      </section>
    </>
  )
}
