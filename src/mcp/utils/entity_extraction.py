# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Extract entities from text for graph-based retrieval.

Uses pattern matching (no ML dependency) for:
- Proper nouns (capitalized multi-word phrases)
- Technology terms (from a curated dictionary)
- Date patterns
- Domain-specific terms

Designed to run at query time with near-zero latency.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Technology / domain vocabulary
# ---------------------------------------------------------------------------
_TECH_TERMS: set[str] = {
    # Languages
    "python", "javascript", "typescript", "rust", "go", "java", "c++",
    "ruby", "php", "swift", "kotlin",
    # Frameworks
    "react", "nextjs", "next.js", "django", "flask", "fastapi", "express",
    "angular", "vue", "svelte", "rails",
    # Databases
    "postgresql", "postgres", "mysql", "mongodb", "redis", "neo4j",
    "chromadb", "elasticsearch", "sqlite",
    # Cloud / infra
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform",
    "vercel", "netlify", "cloudflare",
    # AI / ML
    "openai", "anthropic", "langchain", "llamaindex", "huggingface",
    "pytorch", "tensorflow", "onnx", "transformer", "embeddings",
    "rag", "llm", "gpt", "claude", "gemini",
    # Protocols
    "rest", "graphql", "grpc", "websocket", "mcp", "a2a",
}

# Capitalized phrase: 2-4 consecutive capitalized words (likely proper nouns)
_PROPER_NOUN_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b"
)

# Date patterns: YYYY-MM-DD, Month Day Year, etc.
_DATE_RE = re.compile(
    r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b"
    r"|\b((?:January|February|March|April|May|June|July|August"
    r"|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)

# Version patterns: v1.2.3, 2.0, etc.
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")


def extract_entities(text: str) -> list[dict[str, str]]:
    """Extract entities using pattern matching.

    Returns list of {"text": str, "type": str} where type is one of:
    person, organization, technology, concept, date.

    Deduplicates by lowercased text. Designed for speed at query time.
    """
    entities: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(entity_text: str, entity_type: str) -> None:
        key = entity_text.lower().strip()
        if key and key not in seen and len(key) > 1:
            seen.add(key)
            entities.append({"text": entity_text.strip(), "type": entity_type})

    # 1. Technology terms (case-insensitive exact match on word boundaries)
    text_lower = text.lower()
    words = set(re.findall(r"\b\w+(?:\.\w+)?\b", text_lower))
    for term in _TECH_TERMS:
        if term in words or term.replace(".", "") in words:
            _add(term, "technology")

    # 2. Capitalized phrases (proper nouns — likely people or organizations)
    for match in _PROPER_NOUN_RE.finditer(text):
        phrase = match.group(1)
        # Skip common false positives (sentence starters, etc.)
        phrase_lower = phrase.lower()
        if phrase_lower in ("the", "this", "that", "these", "those",
                            "what", "which", "where", "when", "how",
                            "have", "has", "had", "does", "did"):
            continue
        # Heuristic: 2-word capitalized phrases are likely names/orgs
        word_count = len(phrase.split())
        if word_count >= 2:
            entity_type = "person" if word_count == 2 else "organization"
            _add(phrase, entity_type)

    # 3. Date patterns
    for match in _DATE_RE.finditer(text):
        date_str = match.group(1) or match.group(2)
        if date_str:
            _add(date_str, "date")

    # 4. Quoted terms (likely specific concepts or titles)
    for match in re.finditer(r'"([^"]{3,50})"', text):
        _add(match.group(1), "concept")

    return entities


def extract_entity_names(text: str) -> list[str]:
    """Convenience: extract just the entity text values (for graph matching)."""
    return [e["text"] for e in extract_entities(text)]
