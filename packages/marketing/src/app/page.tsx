import Link from "next/link"
import Image from "next/image"
import {
  Brain,
  Database,
  Lock,
  Search,
  Shield,
  ShieldCheck,
  Sparkles,
  Zap,
  Bot,
  Layers,
  ArrowRight,
  FolderOpen,
  Cpu,
  Eye,
  CheckCircle,
} from "lucide-react"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

/* ── Feature grid — 6 cards, consistent height, no images ── */
const FEATURES = [
  {
    icon: Layers,
    title: "Smart Retrieval",
    casual: "Ask anything — Cerid finds the answer across all your files.",
    pro: "Three RAG modes with per-chunk adaptive scoring profiles. Hybrid BM25+vector search with cross-encoder reranking.",
  },
  {
    icon: ShieldCheck,
    title: "Verified Answers",
    casual: "Every response is fact-checked in real time. See what's confirmed and what needs review.",
    pro: "4-type claim detection (factual, recency, evasion, citation) with streaming inline verification and source attribution.",
  },
  {
    icon: Bot,
    title: "Any AI Model",
    casual: "Works with Claude, GPT, Gemini, Llama — or run a free local model with zero API costs.",
    pro: "OpenRouter multi-provider routing, Ollama local LLM (6/8 pipeline stages at $0), guided install wizard.",
  },
  {
    icon: Brain,
    title: "Learns From You",
    casual: "Cerid remembers your preferences, decisions, and facts — making every conversation smarter.",
    pro: "6-type memory salience scoring with conflict resolution. Memories auto-inject alongside KB context.",
  },
  {
    icon: Lock,
    title: "Totally Private",
    casual: "Your data never leaves your computer. No cloud required. No one sees your files.",
    pro: "Self-hosted Docker stack. Optional Fernet encryption at rest. Only query context sent to LLM provider.",
  },
  {
    icon: FolderOpen,
    title: "Easy Import",
    casual: "Point Cerid at a folder and it indexes everything — PDFs, docs, code, notes, even zip files.",
    pro: "Bulk scan with preview, archive extraction, junk filtering, SSE progress streaming, pause/resume controls.",
  },
]

/* ── Showcase items — hero-style graphics from brand assets ── */
const SHOWCASES = [
  { src: "/features-grid.jpg", alt: "Six core capabilities", caption: "Bring Your Own Model. Real-Time Verification. Extensible Architecture." },
  { src: "/icon-grid.jpg", alt: "Cerid icon set", caption: "12 purpose-built feature icons designed for the Cerid ecosystem." },
  { src: "/secure-intel.jpg", alt: "Secure Intelligence", caption: "Cerid AI — Secure Intelligence, Fully Yours." },
]

export default function Home() {
  return (
    <>
      {/* ════════════════════════════════════════════════════════════
          HERO — split layout: headline left, shield right
          ════════════════════════════════════════════════════════════ */}
      <section className="relative overflow-hidden bg-gradient-to-b from-background to-muted/30 py-24 md:py-32">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-16 md:grid-cols-2">
            {/* Left — copy */}
            <div className="text-center md:text-left">
              <div className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-500 inline-flex items-center gap-2 rounded-full border border-border/60 bg-muted/50 px-4 py-1.5 text-sm text-muted-foreground">
                <Sparkles className="h-3.5 w-3.5 text-brand" />
                Open source &middot; Self-hosted &middot; Privacy-first
              </div>

              <h1 className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-150 mt-8 text-4xl font-bold tracking-tight leading-[1.1] sm:text-5xl lg:text-6xl">
                Your Private AI
                <br />
                <span className="bg-gradient-to-r from-brand to-[oklch(0.90_0.14_178)] bg-clip-text text-transparent">Knowledge Companion</span>
              </h1>

              <p className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-300 mt-6 max-w-lg text-lg leading-relaxed text-muted-foreground md:text-xl">
                Turn your files, notes, and documents into a searchable AI assistant
                that actually verifies its answers. Everything stays on your machine.
              </p>

              <div className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-500 mt-10 flex flex-col items-center gap-4 sm:flex-row md:justify-start">
                <Link
                  href="https://github.com/Cerid-AI/cerid-ai"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-12 items-center justify-center gap-2 rounded-lg bg-brand px-7 text-sm font-semibold text-brand-foreground shadow-lg shadow-brand/20 transition-all hover:bg-brand/90 hover:shadow-brand/30"
                >
                  Get Started Free
                  <ArrowRight className="h-4 w-4" />
                </Link>
                <Link
                  href="/features"
                  className="inline-flex h-12 items-center justify-center rounded-lg border border-border bg-background px-7 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground"
                >
                  Explore Features
                </Link>
              </div>

              {/* Tier pills */}
              <div className="animate-in fade-in fill-mode-both duration-700 delay-700 mt-8 flex flex-wrap items-center gap-2.5 md:justify-start justify-center">
                <span className="rounded-full border border-brand/40 bg-brand/10 px-3 py-1 text-[11px] font-semibold tracking-wide text-brand uppercase">Core — Free</span>
                <span className="rounded-full border border-border bg-muted/50 px-3 py-1 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">Pro</span>
                <span className="rounded-full border border-gold bg-gold/10 px-3 py-1 text-[11px] font-semibold tracking-wide text-gold uppercase">Vault — Enterprise</span>
              </div>
            </div>

            {/* Right — shield hero image */}
            <div className="animate-in fade-in zoom-in-95 fill-mode-both duration-700 delay-300 flex justify-center">
              <img
                src="/hero-shield.jpg"
                alt="Cerid — gold shield with glowing teal C"
                className="w-72 rounded-2xl shadow-2xl shadow-brand/15 md:w-80 lg:w-96"
              />
            </div>
          </div>

          {/* Stats — social proof strip */}
          <div className="mt-20 flex flex-wrap items-center justify-center gap-8 text-center">
            {[
              { value: "2,311+", label: "Tests passing" },
              { value: "10", label: "AI agents" },
              { value: "26", label: "MCP tools" },
              { value: "9/9", label: "CI jobs green" },
            ].map((s) => (
              <div key={s.label} className="min-w-[80px]">
                <p className="text-2xl font-bold text-foreground">{s.value}</p>
                <p className="text-xs text-muted-foreground">{s.label}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ════════════════════════════════════════════════════════════
          FEATURES — 6-card grid, dual-audience copy, no images
          ════════════════════════════════════════════════════════════ */}
      <section className="py-24">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
              Everything you need
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-lg text-muted-foreground">
              Powerful enough for enterprise intelligence workflows.
              Simple enough to set up in five minutes.
            </p>
          </div>

          <div className="mt-16 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f, i) => (
              <Card
                key={f.title}
                className="group animate-in fade-in slide-in-from-bottom-2 fill-mode-both border-border bg-card transition-colors hover:border-brand/30"
                style={{ animationDelay: `${i * 80}ms`, animationDuration: "500ms" }}
              >
                <CardHeader className="space-y-3">
                  <div className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-brand/10 text-brand transition-colors group-hover:bg-brand/20">
                    <f.icon className="h-5 w-5" />
                  </div>
                  <CardTitle className="text-lg">{f.title}</CardTitle>
                  <p className="text-sm leading-relaxed text-foreground/90">{f.casual}</p>
                  <p className="text-xs leading-relaxed text-muted-foreground">{f.pro}</p>
                </CardHeader>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* ════════════════════════════════════════════════════════════
          SHOWCASE — brand graphics in a clean 3-column strip
          ════════════════════════════════════════════════════════════ */}
      <section className="border-y border-border bg-muted/30 py-24">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
            {SHOWCASES.map((s) => (
              <div key={s.alt} className="group overflow-hidden rounded-xl border border-border bg-card">
                <img
                  src={s.src}
                  alt={s.alt}
                  className="aspect-[4/3] w-full object-cover transition-transform duration-300 group-hover:scale-[1.02]"
                />
                <p className="px-4 py-3 text-xs text-muted-foreground">{s.caption}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ════════════════════════════════════════════════════════════
          HOW IT WORKS — 4-step visual pipeline
          ════════════════════════════════════════════════════════════ */}
      <section className="py-24">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">How it works</h2>
            <p className="mx-auto mt-4 max-w-xl text-muted-foreground">
              One command to start. Ask a question. Get a verified answer.
            </p>
          </div>

          <div className="mt-16 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
            {[
              { step: "1", icon: FolderOpen, title: "Import your files", desc: "Drop a folder, upload files, or connect your archive. Cerid parses PDFs, docs, code, emails, and more." },
              { step: "2", icon: Search, title: "Ask anything", desc: "Natural language queries search across all your knowledge. Smart retrieval finds the best matches." },
              { step: "3", icon: Eye, title: "See the evidence", desc: "Every answer shows its sources. Inline verification badges tell you what's confirmed and what's uncertain." },
              { step: "4", icon: Brain, title: "Cerid learns", desc: "Facts, decisions, and preferences are remembered. Each conversation makes the system smarter." },
            ].map((s) => (
              <div key={s.step} className="flex flex-col bg-card p-6">
                <div className="flex items-center gap-3">
                  <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand/15 text-sm font-bold text-brand">{s.step}</span>
                  <s.icon className="h-5 w-5 text-muted-foreground" />
                </div>
                <h3 className="mt-4 font-semibold">{s.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ════════════════════════════════════════════════════════════
          TRUST — privacy + architecture side-by-side
          ════════════════════════════════════════════════════════════ */}
      <section className="border-t border-border bg-muted/30 py-24">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-16 md:grid-cols-2">
            <div>
              <h2 className="text-3xl font-bold tracking-tight">
                Built for trust
              </h2>
              <p className="mt-4 text-lg text-muted-foreground">
                Your knowledge base stays on your machine. Cerid never phones home.
                Only the query context you choose is sent to the LLM provider.
              </p>
              <ul className="mt-8 space-y-4">
                {[
                  { icon: Lock, text: "All data stored locally — ChromaDB, Neo4j, Redis on your machine" },
                  { icon: Shield, text: "Optional Fernet encryption at rest for sensitive knowledge" },
                  { icon: Cpu, text: "Ollama local LLM — run 6 of 8 pipeline stages at $0" },
                  { icon: CheckCircle, text: "Open source Apache-2.0 — audit every line of code" },
                ].map((item) => (
                  <li key={item.text} className="flex items-start gap-3 text-sm">
                    <item.icon className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
                    <span className="text-muted-foreground">{item.text}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex justify-center">
              <img
                src="/privacy.jpg"
                alt="Privacy-First — Fully Local, No Cloud"
                className="w-full max-w-sm rounded-xl shadow-xl"
              />
            </div>
          </div>
        </div>
      </section>

      {/* ════════════════════════════════════════════════════════════
          CTA — final call to action
          ════════════════════════════════════════════════════════════ */}
      <section className="py-24">
        <div className="mx-auto max-w-3xl px-6 text-center">
          <img src="/cerid-logo.svg" alt="" className="mx-auto mb-6 h-12 w-12 opacity-60" />
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Ready to own your knowledge?
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-lg text-muted-foreground">
            Clone the repo. Run the setup script. Start your private AI companion in five minutes.
          </p>
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              href="https://github.com/Cerid-AI/cerid-ai"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-12 items-center justify-center gap-2 rounded-lg bg-brand px-7 text-sm font-semibold text-brand-foreground shadow-lg shadow-brand/20 transition-all hover:bg-brand/90"
            >
              View on GitHub
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              href="/pricing"
              className="inline-flex h-12 items-center justify-center rounded-lg border border-border px-7 text-sm font-medium transition-colors hover:bg-accent"
            >
              Compare Plans
            </Link>
          </div>
        </div>
      </section>
    </>
  )
}
