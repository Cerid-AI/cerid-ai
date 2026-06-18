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
from core.utils.time import utcnow_iso
from errors import IngestionError

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
    """Thin pass-through to the canonical core.utils.text helper.

    Kept as a legacy alias so existing ``from utils.metadata import
    _extract_keywords_simple`` callers continue to work. New code should
    import from ``core.utils.text`` directly.
    """
    from core.utils.text import extract_keywords_simple
    return extract_keywords_simple(text, max_keywords)


# ---------------------------------------------------------------------------
# AI-assisted categorization (token-efficient)
# ---------------------------------------------------------------------------

_CONTROL_TAGS = frozenset({"needs-review"})


def _normalize_tags(tags: list[str], domain: str, max_freeform: int = 3) -> list[str]:
    """Phase 5.4 — converge free-form tags on the domain's vocabulary.

    Tags are metadata, not taxonomy, so they stay open-vocabulary at the
    margin — but they must converge on ``TAG_VOCABULARY`` so the sorting /
    filtering surfaces (Phase 6.3) are coherent rather than fragmented across
    ``q3-report`` / ``q3report`` / ``quarterly-report`` variants.

    For each cleaned (lower/hyphen) tag:
      * a vocabulary tag is kept verbatim;
      * a near-miss (difflib ratio ≥ cutoff) is mapped to its canonical
        vocabulary entry;
      * everything else is a free-form tag, capped at ``max_freeform`` so a
        tag-stuffed document can't drown the vocabulary signal.
    Control tags (``needs-review``) are always preserved and never counted
    against the free-form cap. Total is capped at 10 (the ai_categorize cap).
    """
    import difflib

    vocab = [v.strip().lower() for v in config.TAG_VOCABULARY.get(domain, []) if v.strip()]
    vocab_set = set(vocab)

    kept: list[str] = []
    freeform: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        if not isinstance(raw, str):
            continue
        t = raw.strip().lower().replace(" ", "-")
        if not t or t in seen:
            continue
        seen.add(t)
        if t in _CONTROL_TAGS or t in vocab_set:
            kept.append(t)
            continue
        match = difflib.get_close_matches(t, vocab, n=1, cutoff=0.82)
        if match and match[0] not in seen:
            kept.append(match[0])
            seen.add(match[0])
        elif match and match[0] in seen:
            continue  # canonical form already present — drop the variant
        else:
            freeform.append(t)
    return (kept + freeform[:max_freeform])[:10]


def _sample_for_classification(text: str, budget: int) -> str:
    """Sample head + middle + tail of *text* into a classification snippet.

    Head-only truncation (the prior behavior) mis-classified documents whose
    opening looks generic — resumes lead with contact details, trading
    signals with a boilerplate header — because the discriminating content
    sits past the first ``budget`` chars. When the document fits the budget
    it's returned whole. Otherwise the budget is split 50/25/25 across head,
    middle, and tail with elision markers between the slices.
    """
    if len(text) <= budget:
        return text
    head_len = budget // 2
    mid_len = budget // 4
    tail_len = budget - head_len - mid_len
    mid_start = (len(text) - mid_len) // 2
    head = text[:head_len]
    middle = text[mid_start:mid_start + mid_len]
    tail = text[-tail_len:]
    return (
        f"{head}\n[... elided ...]\n{middle}\n[... elided ...]\n{tail}"
    )


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

    # Phase 5.2 — head+middle+tail sampling within the snippet budget.
    # Head-only classification failed on documents whose first ~1500 chars
    # look generic (resumes lead with contact info; trading signals lead with
    # a boilerplate header). Sampling across the document surfaces the
    # discriminating content for sub_category accuracy.
    snippet = _sample_for_classification(text, config.AI_SNIPPET_MAX_CHARS)

    taxonomy_section = _build_taxonomy_prompt_section()
    prompt = (
        f"Classify this document into exactly one domain and one sub-category "
        f"FROM THAT DOMAIN's listed sub-categories.\n\n"
        f"Available taxonomy:\n{taxonomy_section}\n\n"
        f"Pick the most specific sub-category that fits — only fall back to "
        f"'general' when none of the domain's specific sub-categories apply. "
        f"Give a one-line rationale for the sub-category choice.\n"
        f"Report your confidence (0.0-1.0) that the domain is correct.\n\n"
        f"Also suggest up to 5 descriptive tags (lowercase, hyphenated). "
        f"Prefer the 'preferred tags' listed for the chosen domain when they fit. "
        f"You may add 1-2 free-form tags if nothing in the vocabulary matches.\n"
        f"Extract up to 5 keywords, and write a 1-sentence summary.\n\n"
        f"Filename: {filename}\n"
        f"Content:\n{snippet}\n\n"
        f'Respond ONLY with JSON: '
        f'{{"domain": "...", "sub_category": "...", "sub_category_rationale": "...", '
        f'"confidence": 0.0, "tags": ["..."], '
        f'"keywords": ["..."], "summary": "..."}}'
    )

    try:
        # Route via internal LLM when the operator picked a local backend
        # (Ollama or Quenchforge).  Pre-v0.93.8 this branch only fired
        # for "ollama", silently shunting Quenchforge users back to
        # OpenRouter for ingest-time categorization — the per-document
        # LLM call that runs on every ingested file.  Fixed: both local
        # backends route through call_internal_llm, which dispatches
        # via _call_ollama with the right URL.
        if config.INTERNAL_LLM_PROVIDER in ("ollama", "quenchforge"):
            from core.utils.internal_llm import call_internal_llm
            content = await call_internal_llm(
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
        from core.utils.llm_parsing import parse_llm_json
        result = parse_llm_json(content)
        suggested = result.get("domain", "").lower().strip()
        if suggested not in config.DOMAINS:
            logger.warning(f"AI suggested unknown domain '{suggested}', using default")
            suggested = config.DEFAULT_DOMAIN

        # Clean tags: lowercase, strip, limit to 10
        raw_tags = result.get("tags", [])
        tags = [
            t.strip().lower().replace(" ", "-")
            for t in raw_tags
            if isinstance(t, str) and t.strip()
        ][:10]

        # Phase 5.2 — confidence gate. A low-confidence domain pick mis-routes
        # the artifact's chunks (per-domain collection) AND skews every graph
        # lens, so a confident-wrong domain is worse than an honest "general".
        # Below the floor → force general + a `needs-review` tag that drives
        # the Track B correction queue. The sub_category is preserved when the
        # model still gave a usable one — sub_category is metadata on the
        # artifact, not a collection selector, so it's safe to keep.
        try:
            confidence = float(result.get("confidence", 1.0))
        except (ValueError, TypeError):
            confidence = 1.0
        confidence = max(0.0, min(1.0, confidence))
        low_confidence = confidence < config.CATEGORIZE_CONFIDENCE_THRESHOLD
        if low_confidence and suggested != config.DEFAULT_DOMAIN:
            logger.info(
                "ai_categorize confidence %.2f < %.2f for domain '%s' — "
                "demoting to '%s' + needs-review",
                confidence, config.CATEGORIZE_CONFIDENCE_THRESHOLD,
                suggested, config.DEFAULT_DOMAIN,
            )
            suggested = config.DEFAULT_DOMAIN
            if "needs-review" not in tags:
                tags = (tags + ["needs-review"])[:10]

        # Phase 5.4 — converge tags on the (possibly demoted) domain's
        # vocabulary so sorting/filtering surfaces stay coherent. Runs after
        # the confidence gate so needs-review is preserved + normalized against
        # the final domain.
        tags = _normalize_tags(tags, suggested)

        # Validate sub_category against the (possibly demoted) domain's taxonomy
        sub_cat = result.get("sub_category", "").lower().strip()
        domain_info = config.TAXONOMY.get(suggested, {})
        valid_subs = [s.lower() for s in domain_info.get("sub_categories", ["general"])]
        if sub_cat not in valid_subs:
            sub_cat = config.DEFAULT_SUB_CATEGORY

        return {
            "suggested_domain": suggested,
            "sub_category": sub_cat,
            "confidence": confidence,
            "tags": tags,
            "keywords": result.get("keywords", []),
            "summary": result.get("summary", ""),
        }

    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        from core.utils.swallowed import log_swallowed_error
        try:
            from app.deps import get_redis
            _redis = get_redis()
        except Exception:  # noqa: BLE001 — Redis optional in some test contexts
            _redis = None
        log_swallowed_error("ingestion.ai_categorize", e, redis_client=_redis)
        return {}
    except Exception as e:  # noqa: BLE001 — defensive catch for httpx/circuit-breaker errors
        from core.utils.swallowed import log_swallowed_error
        try:
            from app.deps import get_redis
            _redis = get_redis()
        except Exception:  # noqa: BLE001
            _redis = None
        log_swallowed_error("ingestion.ai_categorize", e, redis_client=_redis)
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
