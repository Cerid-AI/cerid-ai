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
    icon: Search,
    title: "Instant Knowledge Search",
    description:
      "Ask questions in plain English and get answers from your own files, notes, and documents in seconds.",
  },
  {
    icon: Bot,
    title: "Smart Assistants That Work Together",
    description:
      "Specialized AI agents handle different tasks — from finding information to checking facts to organizing your knowledge.",
  },
  {
    icon: Database,
    title: "All Your Knowledge, One Place",
    description:
      "Bring together code, documents, financial data, project notes — everything searchable in one private system.",
  },
  {
    icon: Shield,
    title: "Built-In Fact Checking",
    description:
      "Every AI response is automatically verified in real time. See which claims are confirmed, uncertain, or need attention.",
  },
  {
    icon: GitBranch,
    title: "See How Ideas Connect",
    description:
      "Your knowledge isn't just stored — it's connected. Discover relationships between topics, projects, and documents.",
  },
  {
    icon: Zap,
    title: "Always the Right AI Model",
    description:
      "Automatically picks the best AI model for each question — fast models for simple tasks, powerful ones for complex analysis.",
  },
  {
    icon: Layers,
    title: "Deeply Understands Your Questions",
    description:
      "Breaks down complex questions, finds diverse sources, and assembles comprehensive answers — not just keyword matching.",
  },
]

export default function Home() {
  return (
    <>
      {/* Hero */}
      <section className="relative overflow-hidden bg-gradient-to-b from-background to-muted/30 py-24 md:py-32">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <div className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-500 mx-auto inline-flex items-center gap-2 rounded-full border border-border/60 bg-muted/50 px-4 py-1.5 text-sm text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" />
            Open source &middot; Self-hosted &middot; Privacy-first
          </div>

          <h1 className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-150 mt-6 text-4xl font-bold tracking-tight sm:text-5xl md:text-6xl">
            Your AI Knowledge
            <br />
            <span className="text-primary">Companion</span>
          </h1>

          <p className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-300 mx-auto mt-6 max-w-2xl text-lg text-muted-foreground">
            Cerid AI turns your personal files, notes, and documents into a
            searchable AI assistant that actually verifies its answers.
            Everything runs on your computer — your knowledge base stays local,
            only query context is sent to the LLM provider you choose.
          </p>

          <div className="animate-in fade-in slide-in-from-bottom-4 fill-mode-both duration-700 delay-500 mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
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

          {/* Stats strip */}
          <div className="mx-auto mt-12 flex max-w-lg flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-muted-foreground">
            <span>1,950+ tests</span>
            <span className="text-border">·</span>
            <span>9 AI agents</span>
            <span className="text-border">·</span>
            <span>27 tools</span>
            <span className="text-border">·</span>
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
                className="animate-in fade-in slide-in-from-bottom-2 fill-mode-both border-border bg-card"
                style={{ animationDelay: `${i * 100}ms`, animationDuration: "500ms" }}
              >
                <CardHeader>
                  <prop.icon className="mb-2 h-6 w-6 text-primary" />
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
