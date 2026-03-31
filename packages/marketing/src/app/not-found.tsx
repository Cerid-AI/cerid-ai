import Link from "next/link"
import { ArrowLeft } from "lucide-react"
import { BrandShield } from "@/components/brand-shield"

export default function NotFound() {
  return (
    <section className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center bg-circuit">
      <BrandShield variant="core" size={64} animate className="mb-8" />
      <h1 className="text-5xl font-bold tracking-tight">404</h1>
      <p className="mt-4 text-lg text-muted-foreground">
        This page doesn&apos;t exist or has been moved.
      </p>
      <Link
        href="/"
        className="mt-8 inline-flex h-10 items-center gap-2 rounded-lg border border-border px-5 text-sm font-medium transition-colors hover:bg-accent"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to home
      </Link>
    </section>
  )
}
