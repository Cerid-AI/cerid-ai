import type { Metadata } from "next"
import { Geist, Geist_Mono } from "next/font/google"
import { Navbar } from "@/components/navbar"
import { Footer } from "@/components/footer"
import "./globals.css"

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
})

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
})

export const metadata: Metadata = {
  title: "Cerid AI — Privacy-First AI Knowledge Companion",
  description:
    "Self-hosted, privacy-first AI knowledge management. Unify code, finance, projects, and artifacts into a context-aware LLM interface with RAG-powered retrieval.",
  metadataBase: new URL("https://cerid.ai"),
  openGraph: {
    title: "Cerid AI",
    description: "Privacy-First AI Knowledge Companion",
    url: "https://cerid.ai",
    siteName: "Cerid AI",
    type: "website",
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-background text-foreground`}
      >
        <div className="vignette" aria-hidden="true" />
        <Navbar />
        <main className="relative z-[2] min-h-screen">{children}</main>
        <Footer />
      </body>
    </html>
  )
}
