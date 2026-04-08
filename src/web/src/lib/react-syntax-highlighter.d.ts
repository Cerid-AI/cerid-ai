// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Type declarations for react-syntax-highlighter ESM sub-paths
// (the @types package only covers the main entry point)

declare module "react-syntax-highlighter/dist/esm/prism-light" {
  import type { SyntaxHighlighterProps } from "react-syntax-highlighter"
  import type { ComponentType } from "react"

  interface PrismLight extends ComponentType<SyntaxHighlighterProps> {
    registerLanguage(name: string, grammar: unknown): void
  }

  const PrismLight: PrismLight
  export default PrismLight
}

declare module "react-syntax-highlighter/dist/esm/languages/prism/javascript" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/typescript" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/python" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/bash" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/json" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/css" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/markup" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/sql" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/yaml" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/go" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/rust" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/java" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/c" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/cpp" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/csharp" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/markdown" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/diff" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/docker" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/ruby" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/swift" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/kotlin" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/jsx" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/tsx" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/scss" {
  const lang: unknown; export default lang
}
declare module "react-syntax-highlighter/dist/esm/languages/prism/toml" {
  const lang: unknown; export default lang
}

declare module "react-syntax-highlighter/dist/esm/styles/prism/one-dark" {
  const style: Record<string, React.CSSProperties>
  export default style
}
