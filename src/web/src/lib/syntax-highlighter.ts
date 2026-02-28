// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PrismLight wrapper — registers only common languages (~25) instead of all 200+.
 * Reduces the lazy-loaded syntax-highlighting chunk from ~1.6MB to ~200KB.
 *
 * Lazy-loaded via React.lazy in message-bubble.tsx; never imported eagerly.
 */

import PrismLight from "react-syntax-highlighter/dist/esm/prism-light"
import oneDark from "react-syntax-highlighter/dist/esm/styles/prism/one-dark"

// Languages commonly seen in AI chat conversations
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript"
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript"
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx"
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx"
import python from "react-syntax-highlighter/dist/esm/languages/prism/python"
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash"
import json from "react-syntax-highlighter/dist/esm/languages/prism/json"
import css from "react-syntax-highlighter/dist/esm/languages/prism/css"
import scss from "react-syntax-highlighter/dist/esm/languages/prism/scss"
import markup from "react-syntax-highlighter/dist/esm/languages/prism/markup"
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql"
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml"
import toml from "react-syntax-highlighter/dist/esm/languages/prism/toml"
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown"
import diff from "react-syntax-highlighter/dist/esm/languages/prism/diff"
import docker from "react-syntax-highlighter/dist/esm/languages/prism/docker"
import go from "react-syntax-highlighter/dist/esm/languages/prism/go"
import rust from "react-syntax-highlighter/dist/esm/languages/prism/rust"
import java from "react-syntax-highlighter/dist/esm/languages/prism/java"
import c from "react-syntax-highlighter/dist/esm/languages/prism/c"
import cpp from "react-syntax-highlighter/dist/esm/languages/prism/cpp"
import csharp from "react-syntax-highlighter/dist/esm/languages/prism/csharp"
import ruby from "react-syntax-highlighter/dist/esm/languages/prism/ruby"
import swift from "react-syntax-highlighter/dist/esm/languages/prism/swift"
import kotlin from "react-syntax-highlighter/dist/esm/languages/prism/kotlin"

PrismLight.registerLanguage("javascript", javascript)
PrismLight.registerLanguage("js", javascript)
PrismLight.registerLanguage("typescript", typescript)
PrismLight.registerLanguage("ts", typescript)
PrismLight.registerLanguage("jsx", jsx)
PrismLight.registerLanguage("tsx", tsx)
PrismLight.registerLanguage("python", python)
PrismLight.registerLanguage("py", python)
PrismLight.registerLanguage("bash", bash)
PrismLight.registerLanguage("sh", bash)
PrismLight.registerLanguage("shell", bash)
PrismLight.registerLanguage("zsh", bash)
PrismLight.registerLanguage("json", json)
PrismLight.registerLanguage("css", css)
PrismLight.registerLanguage("scss", scss)
PrismLight.registerLanguage("html", markup)
PrismLight.registerLanguage("xml", markup)
PrismLight.registerLanguage("svg", markup)
PrismLight.registerLanguage("markup", markup)
PrismLight.registerLanguage("sql", sql)
PrismLight.registerLanguage("yaml", yaml)
PrismLight.registerLanguage("yml", yaml)
PrismLight.registerLanguage("toml", toml)
PrismLight.registerLanguage("markdown", markdown)
PrismLight.registerLanguage("md", markdown)
PrismLight.registerLanguage("diff", diff)
PrismLight.registerLanguage("dockerfile", docker)
PrismLight.registerLanguage("docker", docker)
PrismLight.registerLanguage("go", go)
PrismLight.registerLanguage("rust", rust)
PrismLight.registerLanguage("rs", rust)
PrismLight.registerLanguage("java", java)
PrismLight.registerLanguage("c", c)
PrismLight.registerLanguage("cpp", cpp)
PrismLight.registerLanguage("csharp", csharp)
PrismLight.registerLanguage("cs", csharp)
PrismLight.registerLanguage("ruby", ruby)
PrismLight.registerLanguage("rb", ruby)
PrismLight.registerLanguage("swift", swift)
PrismLight.registerLanguage("kotlin", kotlin)
PrismLight.registerLanguage("kt", kotlin)

export { PrismLight, oneDark }
