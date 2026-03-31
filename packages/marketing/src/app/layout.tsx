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
  icons: {
    icon: "/favicon.ico",
    apple: "/apple-touch-icon.png",
  },
  openGraph: {
    title: "Cerid AI",
    description: "Privacy-First AI Knowledge Companion",
    url: "https://cerid.ai",
    siteName: "Cerid AI",
    type: "website",
    images: [
      {
        url: "/og-image.jpg",
        width: 1200,
        height: 630,
        alt: "Cerid AI — Privacy-First AI Knowledge Companion",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Cerid AI — Privacy-First AI Knowledge Companion",
    description:
      "Self-hosted, privacy-first AI knowledge management. Unify code, finance, projects, and artifacts into a context-aware LLM interface with RAG-powered retrieval.",
    images: ["/og-image.jpg"],
  },
}

const jsonLd = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      name: "Cerid AI",
      url: "https://cerid.ai",
      logo: "https://cerid.ai/cerid-logo.svg",
      description:
        "Self-hosted, privacy-first AI knowledge management.",
    },
    {
      "@type": "SoftwareApplication",
      name: "Cerid AI",
      applicationCategory: "DeveloperApplication",
      operatingSystem: "macOS, Linux, Windows (WSL2)",
      offers: {
        "@type": "Offer",
        price: "0",
        priceCurrency: "USD",
      },
      description:
        "Unify code, finance, projects, and artifacts into a context-aware LLM interface with RAG-powered retrieval.",
    },
  ],
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
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
        <div className="vignette" aria-hidden="true" />
        <Navbar />
        <main className="relative z-[2] min-h-screen">{children}</main>
        <Footer />
      </body>
    </html>
  )
}
