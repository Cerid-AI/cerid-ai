# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Embedding model configuration.

Provides a factory for ChromaDB-compatible embedding functions.
Currently returns None to use the ChromaDB server default (all-MiniLM-L6-v2).
Future: support pre-computing embeddings client-side with custom models.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import config

logger = logging.getLogger("ai-companion.embeddings")


def get_embedding_function(model_name: Optional[str] = None) -> Any:
    """Return a ChromaDB EmbeddingFunction for the configured model.

    Returns None to use ChromaDB server default (all-MiniLM-L6-v2).
    When a custom model is configured, this will return a
    SentenceTransformerEmbeddingFunction for client-side embedding.

    Args:
        model_name: Override the configured model. If None, uses
                    config.EMBEDDING_MODEL.

    Returns:
        None for server default, or a ChromaDB EmbeddingFunction.
    """
    model_name = model_name or config.EMBEDDING_MODEL

    if model_name == "all-MiniLM-L6-v2":
        return None  # ChromaDB server default

    logger.warning(
        "Custom embedding model %r configured but not yet supported. "
        "Using ChromaDB server default. To use custom models, install "
        "sentence-transformers and update this module.",
        model_name,
    )
    return None
