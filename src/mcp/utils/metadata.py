# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Metadata extraction and AI-assisted categorization.

extract_metadata()  — local-only, no API calls
ai_categorize()     — calls OpenRouter (via llm_client) for domain classification
                      (token-efficient)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import tiktoken

import config
from errors import IngestionError
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.metadata")

_ENCODING = tiktoken.get_encoding("cl100k_base")

# Cache spaCy model — load once, reuse across all calls
_nlp: Any = None


def _get_nlp():
    """Load spaCy model once and cache it. Returns None if unavailable."""
    global _nlp
    if _nlp is not None:
        return _nlp
    try:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
        logger.info("spaCy en_core_web_sm model loaded")
        return _nlp
    except (ImportError, OSError) as e:
        logger.info(f"spaCy model not available, using simple keyword extraction: {e}")
        _nlp = False  # sentinel: tried and failed, don't retry
        return None


# ---------------------------------------------------------------------------
# Local metadata extraction (no API calls)
# ---------------------------------------------------------------------------

def extract_metadata(text: str, filename: str, domain: str) -> dict[str, Any]:
    """
    Extract core metadata from parsed text. No external calls.

    Returns dict with string/int/float values only (ChromaDB compatible).
    Lists are JSON-serialized to strings.
    """
    file_type = Path(filename).suffix.lstrip(".").lower()
    char_count = len(text)
    token_count = len(_ENCODING.encode(text))

    keywords = _extract_keywords(text)

    return {
        "filename": filename,
        "file_type": file_type,
        "domain": domain,
        "ingested_at": utcnow_iso(),
        "char_count": char_count,
        "estimated_tokens": token_count,
        "keywords": json.dumps(keywords),  # JSON string — ChromaDB can't store lists
        "summary": _extract_summary(text),
    }


def extract_metadata_minimal(text: str, filename: str, domain: str) -> dict[str, Any]:
    """Fast-path metadata for wizard / bulk ingest — skips spaCy + tiktoken.

    Returns a structurally-identical dict to extract_metadata() but
    substitutes cheap filename-derived keywords and a raw-prefix summary
    for the NLP-heavy versions. estimated_tokens uses a rough char/4
    heuristic which is ±15% for English prose.

    Use this when the caller is willing to trade keyword/summary quality
    for sub-100ms ingest latency (wizard first-run, batch re-ingest).
    The resulting artifact can be re-enriched later by the curator agent.
    """
    file_type = Path(filename).suffix.lstrip(".").lower()
    # Filename-derived keyword hint: split stem on word separators, drop
    # short tokens. Stable and cheap — zero NLP dependencies.
    stem = Path(filename).stem
    stem_words = [w.lower() for w in stem.replace("-", "_").split("_") if len(w) > 2]
    return {
        "filename": filename,
        "file_type": file_type,
        "domain": domain,
        "ingested_at": utcnow_iso(),
        "char_count": len(text),
        "estimated_tokens": len(text) // 4,
        "keywords": json.dumps(stem_words[:5]),
        "summary": _extract_summary(text),
        "metadata_mode": "minimal",  # audit trail — curator can re-enrich
    }


def _extract_summary(text: str, max_len: int = 200) -> str:
    """Extract a meaningful summary from the first portion of text.

    Prefers the first complete sentence over a hard truncation at max_len.
    Strips whitespace, control characters, and Markdown headings.
    """
    import re

    # Collapse whitespace and strip control chars
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text[:500])
    clean = re.sub(r"\s+", " ", clean).strip()
    # Strip leading Markdown headings
    clean = re.sub(r"^#{1,6}\s+", "", clean)

    if len(clean) <= max_len:
        return clean

    # Try to end at the first sentence boundary
    match = re.search(r"[.!?]\s", clean[:max_len])
    if match:
        return clean[: match.end()].strip()

    # Fall back to word boundary near max_len
    truncated = clean[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        return truncated[:last_space] + "..."
    return truncated + "..."


def _extract_keywords(text: str, max_keywords: int = 10) -> list[str]:
    """
    Extract keywords using spaCy NER if available, else simple word frequency.
    Uses cached model to avoid reloading on every call.
    """
    nlp = _get_nlp()
    if nlp is None or nlp is False:
        return _extract_keywords_simple(text, max_keywords)

    # Use first 5000 chars to keep it fast
    doc = nlp(text[:5000])
    entities = [ent.text.strip() for ent in doc.ents if len(ent.text.strip()) > 2]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for e in entities:
        lower = e.lower()
        if lower not in seen:
            seen.add(lower)
            unique.append(e)
    return unique[:max_keywords]


def _extract_keywords_simple(text: str, max_keywords: int = 10) -> list[str]:
    """Fallback keyword extraction using word frequency."""
    import re
    from collections import Counter

    stop_words = {
        "the", "a", "an", "is", "it", "in", "on", "at", "to", "for",
        "of", "and", "or", "but", "with", "this", "that", "from", "by",
        "as", "are", "was", "were", "be", "been", "being", "have", "has",
        "had", "do", "does", "did", "will", "would", "could", "should",
        "may", "might", "can", "not", "no", "so", "if", "then", "than",
        "its", "my", "your", "we", "they", "he", "she", "i", "you",
        "also", "just", "more", "very", "when", "where", "what", "how",
        "all", "each", "every", "both", "few", "most", "other", "some",
        "such", "only", "own", "same", "too", "any", "about", "after",
        "before", "between", "into", "through", "during", "above", "below",
        "out", "off", "over", "under", "again", "further", "once", "here",
        "there", "why", "which", "who", "whom", "these", "those", "them",
        "their", "our", "his", "her", "up", "down", "get", "got", "make",
        "like", "use", "used", "using", "new", "one", "two", "see", "way",
        "well", "back", "even", "give", "take", "come", "still", "know",
        "need", "want", "try", "ask", "work", "first", "last", "long",
        "great", "little", "right", "big", "high", "low", "small", "large",
        "next", "early", "young", "old", "important", "public", "bad",
        "good", "much", "many", "set", "say", "said", "let", "keep",
        "put", "think", "thought", "tell", "told", "find", "found",
    }
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text[:5000].lower())
    filtered = [w for w in words if w not in stop_words]
    common = Counter(filtered).most_common(max_keywords)
    return [word for word, _ in common]


# ---------------------------------------------------------------------------
# AI-assisted categorization (token-efficient)
# ---------------------------------------------------------------------------

def _build_taxonomy_prompt_section() -> str:
    """Build the taxonomy description for the AI categorization prompt."""
    lines = []
    for domain_name, info in config.TAXONOMY.items():
        sub_cats = info.get("sub_categories", ["general"])
        desc = info.get("description", "")
        vocab = config.TAG_VOCABULARY.get(domain_name, [])
        line = f"  {domain_name} ({desc}): sub-categories = {', '.join(sub_cats)}"
        if vocab:
            line += f"; preferred tags = {', '.join(vocab[:10])}"
        lines.append(line)
    return "\n".join(lines)


async def ai_categorize(
    text: str,
    filename: str,
    mode: str | None = None,
) -> dict[str, Any]:
    """
    Classify a document using an OpenRouter-hosted LLM via ``core.utils.llm_client``.
    Token-efficient: sends a snippet, not the full document.

    Args:
        text: Full document text.
        filename: Original filename.
        mode: "smart" (Llama free) or "pro" (Claude). None = env default.

    Returns:
        {
            "suggested_domain": str,
            "sub_category": str,
            "tags": list[str],
            "keywords": list[str],
            "summary": str,
        }
        Empty dict on failure (graceful fallback).
    """
    mode = mode or config.CATEGORIZE_MODE
    if mode == "manual":
        return {}

    model_id = config.CATEGORIZE_MODELS.get(mode, config.CATEGORIZE_MODELS["smart"])

    # Token efficiency: truncate to snippet
    snippet = text[:config.AI_SNIPPET_MAX_CHARS]
    if len(text) > config.AI_SNIPPET_MAX_CHARS:
        snippet += "\n[... truncated for classification ...]"

    taxonomy_section = _build_taxonomy_prompt_section()
    prompt = (
        f"Classify this document into exactly one domain and sub-category.\n\n"
        f"Available taxonomy:\n{taxonomy_section}\n\n"
        f"Also suggest up to 5 descriptive tags (lowercase, hyphenated). "
        f"Prefer the 'preferred tags' listed for the chosen domain when they fit. "
        f"You may add 1-2 free-form tags if nothing in the vocabulary matches.\n"
        f"Extract up to 5 keywords, and write a 1-sentence summary.\n\n"
        f"Filename: {filename}\n"
        f"Content:\n{snippet}\n\n"
        f'Respond ONLY with JSON: '
        f'{{"domain": "...", "sub_category": "...", "tags": ["..."], '
        f'"keywords": ["..."], "summary": "..."}}'
    )

    try:
        # Route via internal LLM when configured (e.g. Ollama for free local inference)
        if config.INTERNAL_LLM_PROVIDER == "ollama":
            from utils.internal_llm import call_internal_llm
            content = await call_internal_llm(  # type: ignore[call-arg]
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
                response_format={"type": "json_object"},
                stage="topic_extraction",
            )
        else:
            from core.utils.llm_client import call_llm
            content = await call_llm(
                [{"role": "user", "content": prompt}],
                model=model_id,
                temperature=0.1,
                max_tokens=200,
                timeout=30.0,
                response_format={"type": "json_object"},
                breaker_name="bifrost-claims",
            )
        from utils.llm_parsing import parse_llm_json
        result = parse_llm_json(content)
        suggested = result.get("domain", "").lower().strip()
        if suggested not in config.DOMAINS:
            logger.warning(f"AI suggested unknown domain '{suggested}', using default")
            suggested = config.DEFAULT_DOMAIN

        # Validate sub_category against taxonomy
        sub_cat = result.get("sub_category", "").lower().strip()
        domain_info = config.TAXONOMY.get(suggested, {})
        valid_subs = [s.lower() for s in domain_info.get("sub_categories", ["general"])]
        if sub_cat not in valid_subs:
            sub_cat = config.DEFAULT_SUB_CATEGORY

        # Clean tags: lowercase, strip, limit to 10
        raw_tags = result.get("tags", [])
        tags = [
            t.strip().lower().replace(" ", "-")
            for t in raw_tags
            if isinstance(t, str) and t.strip()
        ][:10]

        return {
            "suggested_domain": suggested,
            "sub_category": sub_cat,
            "tags": tags,
            "keywords": result.get("keywords", []),
            "summary": result.get("summary", ""),
        }

    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"AI categorization failed: {e}")
        return {}
    except Exception as e:  # noqa: BLE001 — defensive catch for httpx/circuit-breaker errors
        logger.error(f"AI categorization failed (unexpected): {e}")
        return {}


# ---------------------------------------------------------------------------
# Tag quality scoring
# ---------------------------------------------------------------------------

def score_tags(tags: list[str], domain: str) -> float:
    """Score a tag list based on vocabulary membership and diversity.

    Returns a float in [0.0, 1.0]:
      - 1.0 = all tags from vocabulary, good diversity
      - 0.0 = no tags at all

    Scoring:
      - Each vocabulary tag contributes 0.2 (up to 1.0)
      - Each free-form tag contributes 0.1 (up to 0.5)
      - Capped at 1.0
    """
    if not tags:
        return 0.0

    vocab = set(config.TAG_VOCABULARY.get(domain, []))
    score = 0.0
    for tag in tags[:10]:  # cap at 10
        tag_lower = tag.strip().lower()
        if tag_lower in vocab:
            score += 0.2
        else:
            score += 0.1
    return min(1.0, round(score, 2))
