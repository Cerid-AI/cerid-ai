"""
Metadata extraction and AI-assisted categorization.

extract_metadata()  — local-only, no API calls
ai_categorize()     — calls Bifrost for domain classification (token-efficient)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import tiktoken

import config

logger = logging.getLogger("ai-companion.metadata")

_ENCODING = tiktoken.get_encoding("cl100k_base")

# Cache spaCy model — load once, reuse across all calls
_nlp = None


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

def extract_metadata(text: str, filename: str, domain: str) -> Dict[str, Any]:
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
        "ingested_at": datetime.utcnow().isoformat(),
        "char_count": char_count,
        "estimated_tokens": token_count,
        "keywords": json.dumps(keywords),  # JSON string — ChromaDB can't store lists
        "summary": text[:200].replace("\n", " ").strip(),
    }


def _extract_keywords(text: str, max_keywords: int = 10) -> List[str]:
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


def _extract_keywords_simple(text: str, max_keywords: int = 10) -> List[str]:
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

async def ai_categorize(
    text: str,
    filename: str,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Classify a document using Bifrost AI. Token-efficient: sends a snippet,
    not the full document.

    Args:
        text: Full document text.
        filename: Original filename.
        mode: "smart" (Llama free) or "pro" (Claude). None = env default.

    Returns:
        {"suggested_domain": str, "keywords": list, "summary": str}
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

    domain_list = ", ".join(config.DOMAINS)
    prompt = (
        f"Classify this document into exactly one domain: {domain_list}.\n"
        f"Also extract up to 5 keywords and a 1-sentence summary.\n\n"
        f"Filename: {filename}\n"
        f"Content:\n{snippet}\n\n"
        f'Respond ONLY with JSON: {{"domain": "...", "keywords": ["..."], "summary": "..."}}'
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{config.BIFROST_URL}/chat/completions",
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 150,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        result = json.loads(content)
        suggested = result.get("domain", "").lower().strip()
        if suggested not in config.DOMAINS:
            logger.warning(f"AI suggested unknown domain '{suggested}', using default")
            suggested = config.DEFAULT_DOMAIN

        return {
            "suggested_domain": suggested,
            "keywords": result.get("keywords", []),
            "summary": result.get("summary", ""),
        }

    except Exception as e:
        logger.error(f"AI categorization failed: {e}")
        return {}
