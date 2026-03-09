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
  ArrowRight,
} from "lucide-react"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

const VALUE_PROPS = [
  {
    icon: Search,
    title: "RAG-Powered Retrieval",
    description:
      "Hybrid BM25s + vector search with semantic chunking. Your knowledge base, instantly accessible.",
  },
  {
    icon: Bot,
    title: "9 Intelligent Agents",
    description:
      "Query, triage, curator, rectify, audit, maintenance, hallucination, memory, and Self-RAG agents work together.",
  },
  {
    icon: Database,
    title: "Multi-Domain Knowledge",
    description:
      "Code, finance, projects, artifacts — unify all your knowledge in one context-aware system.",
  },
  {
    icon: Shield,
    title: "Streaming Verification",
    description:
      "Real-time claim verification with 4 claim types: evasion, citation, recency, and ignorance detection.",
  },
  {
    icon: GitBranch,
    title: "Knowledge Graph",
    description:
      "Neo4j-backed relationships between artifacts. See connections across your entire knowledge base.",
  },
  {
    icon: Zap,
    title: "Smart Model Routing",
    description:
      "Capability-based model scoring with three-way routing. The right model for every query.",
  },
]

export default function Home() {
  return (
    <>
      {/* Hero */}
      <section className="relative overflow-hidden bg-gradient-to-b from-background to-muted/30 py-24 md:py-32">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <div className="mx-auto inline-flex items-center gap-2 rounded-full border border-border/60 bg-muted/50 px-4 py-1.5 text-sm text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" />
            Open source &middot; Self-hosted &middot; Privacy-first
          </div>

          <h1 className="mt-6 text-4xl font-bold tracking-tight sm:text-5xl md:text-6xl">
            Your AI Knowledge
            <br />
            <span className="text-primary">Companion</span>
          </h1>

          <p className="mx-auto mt-6 max-w-2xl text-lg text-muted-foreground">
            Cerid AI unifies your knowledge bases into a context-aware LLM
            interface with RAG-powered retrieval, intelligent agents, and
            real-time verification. All data stays on your machine.
          </p>

          <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link
              href="https://github.com/sunrunnerfire/cerid-ai"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-11 items-center justify-center gap-2 rounded-md bg-primary px-6 text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90"
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
        </div>
      </section>

      {/* Value Props */}
      <section className="py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight">
              Everything you need for AI-powered knowledge
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-muted-foreground">
              A complete system for ingesting, organizing, retrieving, and
              verifying knowledge — built for privacy and performance.
            </p>
          </div>

          <div className="mt-12 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {VALUE_PROPS.map((prop) => (
              <Card key={prop.title} className="border-border bg-card">
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
                Microservices architecture with Docker Compose orchestration.
                FastAPI backend, React 19 frontend, Neo4j knowledge graph,
                ChromaDB vector store, and Redis caching — all running locally.
              </p>
              <ul className="mt-6 space-y-3 text-sm text-muted-foreground">
                <li className="flex items-center gap-2">
                  <Brain className="h-4 w-4 text-primary" />
                  FastAPI + Python 3.11 MCP Server
                </li>
                <li className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-primary" />
                  Neo4j Graph + ChromaDB Vectors + Redis Cache
                </li>
                <li className="flex items-center gap-2">
                  <Lock className="h-4 w-4 text-primary" />
                  Your data never leaves your machine
                </li>
                <li className="flex items-center gap-2">
                  <Zap className="h-4 w-4 text-primary" />
                  Bifrost LLM gateway with semantic routing
                </li>
              </ul>
            </div>
            <div className="rounded-lg border border-border bg-card p-6 font-mono text-sm">
              <div className="text-muted-foreground">
                <p className="text-foreground">$ ./scripts/start-cerid.sh</p>
                <p className="mt-2 text-green-600 dark:text-green-400">
                  [1/4] Infrastructure ✓
                </p>
                <p className="text-green-600 dark:text-green-400">
                  [2/4] Bifrost LLM Gateway ✓
                </p>
                <p className="text-green-600 dark:text-green-400">
                  [3/4] MCP Server ✓
                </p>
                <p className="text-green-600 dark:text-green-400">
                  [4/4] React GUI ✓
                </p>
                <p className="mt-2 text-foreground">
                  All services healthy. Open http://localhost:3000
                </p>
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
            href="https://github.com/sunrunnerfire/cerid-ai"
            target="_blank"
            rel="noopener noreferrer"
            className="mt-8 inline-flex h-11 items-center justify-center gap-2 rounded-md bg-primary px-6 text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90"
          >
            View on GitHub
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </section>
    </>
  )
}
