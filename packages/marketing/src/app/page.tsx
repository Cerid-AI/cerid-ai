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
} from "lucide-react"
import { Card, CardHeader, CardTitle } from "@/components/ui/card"
import { BrandShield } from "@/components/brand-shield"

/* ── Features — casual copy only, technical details on /features ── */
const FEATURES = [
  {
    icon: Layers,
    title: "Smart Retrieval",
    desc: "Ask anything — Cerid searches all your files and finds the best answer, adapting its strategy to each document type.",
  },
  {
    icon: ShieldCheck,
    title: "Verified Answers",
    desc: "Every response is fact-checked in real time. Inline badges show what's confirmed, uncertain, or needs review.",
  },
  {
    icon: Bot,
    title: "Any AI Model",
    desc: "Claude, GPT, Gemini, Llama — or run a free local model with Ollama. Zero lock-in, zero mandatory API costs.",
  },
  {
    icon: Brain,
    title: "Learns From You",
    desc: "Cerid remembers your preferences, decisions, and key facts. Every conversation makes it smarter.",
  },
  {
    icon: Lock,
    title: "Totally Private",
    desc: "Your data never leaves your computer. No cloud. No telemetry. Only the query context you choose goes to the LLM.",
  },
  {
    icon: FolderOpen,
    title: "Easy Import",
    desc: "Point at a folder — Cerid scans, previews, and indexes everything. PDFs, docs, code, notes, even zip archives.",
  },
]

export default function Home() {
  return (
    <>
      {/* ══════════════════════════════════════════════════════════════
          HERO — brand shield with glow, gradient wordmark, circuit bg
          ══════════════════════════════════════════════════════════════ */}
      <section className="relative overflow-hidden py-28 md:py-36 bg-circuit">
        {/* Radial glow behind shield */}
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_40%,#00E5D8/6,transparent_70%)]" />

        <div className="relative mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-16 md:grid-cols-2">
            {/* Left — copy */}
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
                <Link
                  href="/features"
                  className="inline-flex h-12 items-center rounded-lg border border-border px-7 text-sm font-medium transition-colors hover:bg-accent"
                >
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

            {/* Right — animated brand shield */}
            <div className="flex justify-center">
              <div className="glow-teal rounded-2xl">
                <img
                  src="/hero-shield.jpg"
                  alt="Cerid — gold shield with glowing teal C"
                  className="float w-64 rounded-2xl md:w-72 lg:w-80"
                />
              </div>
            </div>
          </div>

          {/* Stats strip */}
          <div className="mt-24 flex flex-wrap items-center justify-center gap-10 text-center">
            {[
              { value: "2,311+", label: "Tests" },
              { value: "10", label: "AI Agents" },
              { value: "26", label: "MCP Tools" },
              { value: "9/9", label: "CI Green" },
            ].map((s) => (
              <div key={s.label} className="min-w-[70px]">
                <p className="text-2xl font-bold text-brand">{s.value}</p>
                <p className="mt-1 text-xs text-muted-foreground">{s.label}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════════════════════════════════
          FEATURES — 6 cards, clean icons, casual copy
          Link to /features for technical details
          ══════════════════════════════════════════════════════════════ */}
      <section className="py-28 border-t divider-gold">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
              Everything you need
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-lg text-muted-foreground">
              Powerful enough for enterprise intelligence.
              Simple enough to set up in five minutes.
            </p>
          </div>

          <div className="mt-16 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f, i) => (
              <Card
                key={f.title}
                className="group border-border bg-card transition-all hover:border-brand/30 hover:shadow-lg hover:shadow-brand/5"
              >
                <CardHeader className="space-y-3">
                  <div className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-brand/10 text-brand transition-colors group-hover:bg-brand/20">
                    <f.icon className="h-5 w-5" />
                  </div>
                  <CardTitle className="text-lg">{f.title}</CardTitle>
                  <p className="text-sm leading-relaxed text-muted-foreground">{f.desc}</p>
                </CardHeader>
              </Card>
            ))}
          </div>

          <div className="mt-10 text-center">
            <Link
              href="/features"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-brand hover:text-brand/80 transition-colors"
            >
              View all features with technical details
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════════════════════════════════
          BRAND SHOWCASE — 3 graphics in cinematic strip
          ══════════════════════════════════════════════════════════════ */}
      <section className="border-y divider-gold bg-muted/20 py-28 bg-circuit">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center mb-16">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">Designed for the mission</h2>
            <p className="mt-4 text-muted-foreground">IC-grade architecture. Consumer-grade simplicity.</p>
          </div>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
            {[
              { src: "/features-grid.jpg", alt: "Feature capabilities grid" },
              { src: "/icon-grid.jpg", alt: "Cerid icon system" },
              { src: "/secure-intel.jpg", alt: "Secure Intelligence" },
            ].map((img) => (
              <div key={img.alt} className="group overflow-hidden rounded-xl border border-border/50 bg-card">
                <img
                  src={img.src}
                  alt={img.alt}
                  className="aspect-[4/3] w-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
                  loading="lazy"
                />
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════════════════════════════════
          HOW IT WORKS — 4 steps
          ══════════════════════════════════════════════════════════════ */}
      <section className="py-28">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">How it works</h2>
            <p className="mt-4 text-muted-foreground">One command to start. Ask a question. Get a verified answer.</p>
          </div>

          <div className="mt-16 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
            {[
              { n: "1", icon: FolderOpen, title: "Import your files", desc: "Drop a folder or upload files. Cerid parses PDFs, docs, code, emails, and archives automatically." },
              { n: "2", icon: Search, title: "Ask anything", desc: "Natural language queries search all your knowledge. Adaptive retrieval matches the right strategy to each document." },
              { n: "3", icon: Eye, title: "See the evidence", desc: "Inline verification badges on every claim. Click any footnote to see the source, confidence score, and reasoning." },
              { n: "4", icon: Brain, title: "Cerid learns", desc: "Facts, decisions, and preferences are remembered across sessions. Each conversation makes the system smarter." },
            ].map((s) => (
              <div key={s.n} className="flex flex-col bg-card p-7">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-brand/15 text-sm font-bold text-brand">{s.n}</span>
                  <s.icon className="h-5 w-5 text-muted-foreground" />
                </div>
                <h3 className="mt-5 text-base font-semibold">{s.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════════════════════════════════
          TRUST — privacy + architecture
          ══════════════════════════════════════════════════════════════ */}
      <section className="border-t divider-gold bg-muted/20 py-28">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-16 md:grid-cols-2">
            <div>
              <h2 className="text-3xl font-bold tracking-tight">Built for trust</h2>
              <p className="mt-4 text-lg text-muted-foreground">
                Your knowledge base stays on your machine. Cerid never phones home.
              </p>
              <ul className="mt-8 space-y-5">
                {[
                  { icon: Lock, text: "All data stored locally — ChromaDB, Neo4j, Redis on your machine" },
                  { icon: ShieldCheck, text: "Optional Fernet encryption at rest for sensitive knowledge bases" },
                  { icon: Cpu, text: "Ollama local LLM — run 6 of 8 pipeline stages at $0 cost" },
                  { icon: CheckCircle, text: "Open source Apache-2.0 — audit every line of code" },
                  { icon: Zap, text: "Smart model routing — always uses the right model for the task" },
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
              <img
                src="/privacy.jpg"
                alt="Privacy-First — Fully Local, No Cloud"
                className="float w-full max-w-md rounded-xl border border-border/30 shadow-xl"
                loading="lazy"
              />
            </div>
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════════════════════════════════
          CTA
          ══════════════════════════════════════════════════════════════ */}
      <section className="py-28 bg-circuit">
        <div className="mx-auto max-w-3xl px-6 text-center">
          <BrandShield variant="vault" size={56} animate className="mx-auto mb-8" />
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Ready to own your knowledge?
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-lg text-muted-foreground">
            Clone the repo. Run the setup script. Start your private AI companion in under five minutes.
          </p>
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link
              href="https://github.com/Cerid-AI/cerid-ai"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-12 items-center gap-2 rounded-lg bg-brand px-7 text-sm font-semibold text-brand-foreground shadow-lg shadow-brand/20 transition-all hover:bg-brand/90"
            >
              View on GitHub
              <ArrowRight className="h-4 w-4" />
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
