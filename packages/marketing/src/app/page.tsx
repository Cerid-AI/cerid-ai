import Link from "next/link"
import {
  Brain,
  Database,
  Lock,
  Search,
  Shield,
  Sparkles,
  Zap,
  Bot,
  GitBranch,
  Layers,
  ArrowRight,
} from "lucide-react"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

const VALUE_PROPS = [
  {
    icon: Layers,
    title: "Unified RAG Modes",
    description:
      "Three retrieval modes — Manual, Smart, and Custom Smart — with per-chunk adaptive scoring profiles that match the right strategy to each document type.",
    image: "/features-icons.jpg",
  },
  {
    icon: Shield,
    title: "Real-Time Verification",
    description:
      "4-type claim detection (factual, recency, evasion, citation) with streaming inline verification badges. Every response is fact-checked against your KB.",
    image: "/verification.jpg",
  },
  {
    icon: Bot,
    title: "Bring Your Own Model",
    description:
      "OpenRouter, Ollama local ($0 pipeline costs), or any provider. Guided install wizard sets up local LLM in minutes. Smart routing picks the right model automatically.",
    image: "/byom.jpg",
  },
  {
    icon: Brain,
    title: "Memory Layer",
    description:
      "6-type salience scoring — empirical facts, decisions, preferences, project context, temporal events, and conversational insights. Memories enrich every query.",
  },
  {
    icon: Database,
    title: "Bulk Import",
    description:
      "Scan entire folders with preview and estimation. Archive extraction (zip/tar), junk filtering, SSE progress streaming, and pause/resume controls.",
  },
  {
    icon: Lock,
    title: "Privacy-First",
    description:
      "All data stays local. Knowledge base on your machine. Optional Ollama for $0 pipeline costs. Only query context sent to the LLM provider you choose.",
    image: "/privacy.jpg",
  },
  {
    icon: Search,
    title: "Extensible Architecture",
    description:
      "32 routers, 26 MCP tools, 10 agents, pluggable data sources. Multi-KB namespace support. Docker Compose orchestration with one-command startup.",
    image: "/architecture.jpg",
  },
]

export default function Home() {
  return (
    <>
      {/* Hero */}
      <section className="relative overflow-hidden bg-gradient-to-b from-background to-muted/30 py-24 md:py-32">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-12 md:grid-cols-2">
            <div className="text-center md:text-left">
              <div className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-500 inline-flex items-center gap-2 rounded-full border border-border/60 bg-muted/50 px-4 py-1.5 text-sm text-muted-foreground">
                <Sparkles className="h-3.5 w-3.5" />
                Open source &middot; Self-hosted &middot; Privacy-first
              </div>

              <h1 className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-150 mt-6 text-4xl font-bold tracking-tight sm:text-5xl md:text-6xl">
                <span className="bg-gradient-to-r from-brand to-[oklch(0.90_0.14_178)] bg-clip-text text-transparent">Cerid</span>
                {" \u2014 "}Your Private AI
                <br />
                Knowledge Companion
              </h1>

              <p className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-300 mt-6 max-w-xl text-lg text-muted-foreground">
                Smart. Extensible. Mission Assured. Turn your files, notes, and documents
                into a searchable AI assistant that verifies its answers. Everything stays local.
              </p>

              <div className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-500 mt-10 flex flex-col items-center gap-4 sm:flex-row md:justify-start">
                <Link
                  href="https://github.com/Cerid-AI/cerid-ai"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-11 items-center justify-center gap-2 rounded-md bg-brand px-6 text-sm font-medium text-brand-foreground shadow transition-colors hover:bg-brand/90"
                >
                  Get Started
                  <ArrowRight className="h-4 w-4" />
                </Link>
                <Link
                  href="/features"
                  className="inline-flex h-11 items-center justify-center rounded-md border border-input bg-background px-6 text-sm font-medium shadow-sm transition-colors hover:bg-accent hover:text-accent-foreground"
                >
                  Explore Features
                </Link>
              </div>

              {/* Tier badges */}
              <div className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-700 mt-8 flex flex-wrap items-center gap-3 md:justify-start justify-center">
                <span className="rounded-full border border-brand/30 bg-brand/10 px-3 py-1 text-xs font-medium text-brand">Core &mdash; Free</span>
                <span className="rounded-full border border-muted-foreground/30 bg-muted/50 px-3 py-1 text-xs font-medium text-muted-foreground">Pro &mdash; Coming Soon</span>
                <span className="rounded-full border border-gold bg-gold/10 px-3 py-1 text-xs font-medium text-gold">Vault &mdash; Enterprise</span>
              </div>
            </div>

            {/* Hero image */}
            <div className="animate-in fade-in slide-in-from-right-4 fill-mode-both duration-700 delay-300 flex justify-center">
              <img
                src="/hero-shield.jpg"
                alt="Cerid AI — Shield with glowing C"
                className="max-w-xs rounded-2xl shadow-2xl shadow-brand/10 md:max-w-sm"
              />
            </div>
          </div>

          {/* Stats strip */}
          <div className="mx-auto mt-16 flex max-w-lg flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-muted-foreground">
            <span>2,311+ tests</span>
            <span className="text-border">&middot;</span>
            <span>10 AI agents</span>
            <span className="text-border">&middot;</span>
            <span>26 tools</span>
            <span className="text-border">&middot;</span>
            <span>Verified responses</span>
          </div>
        </div>
      </section>

      {/* Value Props */}
      <section className="py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight">
              Everything you need, nothing you don&apos;t
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-muted-foreground">
              A complete system for searching, organizing, and verifying your
              knowledge — built for privacy and designed to be simple.
            </p>
          </div>

          <div className="mt-12 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {VALUE_PROPS.map((prop, i) => (
              <Card
                key={prop.title}
                className="animate-in fade-in slide-in-from-bottom-2 fill-mode-both border-border bg-card overflow-hidden"
                style={{ animationDelay: `${i * 100}ms`, animationDuration: "500ms" }}
              >
                {prop.image && (
                  <img src={prop.image} alt={prop.title} className="h-40 w-full object-cover" />
                )}
                <CardHeader>
                  <prop.icon className="mb-2 h-6 w-6 text-brand" />
                  <CardTitle className="text-lg">{prop.title}</CardTitle>
                  <CardDescription>{prop.description}</CardDescription>
                </CardHeader>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Architecture */}
      <section className="border-t border-border bg-muted/50 py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-12 md:grid-cols-2">
            <div>
              <h2 className="text-3xl font-bold tracking-tight">
                Built on proven foundations
              </h2>
              <p className="mt-4 text-muted-foreground">
                Multiple services work together on your machine — a modern API
                server, a knowledge graph, a search engine, and a smart AI model
                router. One command starts everything.
              </p>
              <ul className="mt-6 space-y-3 text-sm text-muted-foreground">
                <li className="flex items-center gap-2">
                  <Brain className="h-4 w-4 text-primary" />
                  Fast, modern API server
                </li>
                <li className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-primary" />
                  Knowledge graph + search engine + fast cache
                </li>
                <li className="flex items-center gap-2">
                  <Lock className="h-4 w-4 text-primary" />
                  Your knowledge base stays on your machine
                </li>
                <li className="flex items-center gap-2">
                  <Zap className="h-4 w-4 text-primary" />
                  Smart AI model router
                </li>
              </ul>
            </div>
            <div className="rounded-lg border border-border bg-gradient-to-br from-card to-muted/50 p-6">
              <div className="aspect-[16/10] flex items-center justify-center rounded-md bg-muted/30 border border-dashed border-border">
                <div className="text-center text-muted-foreground">
                  <Brain className="mx-auto h-8 w-8 mb-2" />
                  <p className="text-sm font-medium">App Screenshot</p>
                  <p className="text-xs">Coming soon</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <h2 className="text-3xl font-bold tracking-tight">
            Ready to take control of your knowledge?
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-muted-foreground">
            Cerid AI is open source and self-hosted. Clone the repo, run the
            setup script, and start building your personal knowledge companion.
          </p>
          <Link
            href="https://github.com/Cerid-AI/cerid-ai"
            target="_blank"
            rel="noopener noreferrer"
            className="mt-8 inline-flex h-11 items-center justify-center gap-2 rounded-md bg-brand px-6 text-sm font-medium text-brand-foreground shadow transition-colors hover:bg-brand/90"
          >
            View on GitHub
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </section>
    </>
  )
}
