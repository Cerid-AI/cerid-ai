# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Cache-first HuggingFace file resolution shared by ONNX model loaders.

``hf_hub_download`` defaults to a HEAD call against the Hub (to check for a
newer revision) before falling back to the local cache. The reranker, NLI,
and embedding loaders (``core.retrieval.reranker``, ``core.utils.nli``,
``core.utils.embeddings``) already pin a resolved model + filename and
re-load the same cached file on every container boot, so that HEAD call is
pure overhead — and a hard failure on an offline box. ``resolve_hf_file``
tries the local cache first and only reaches the network when the file is
genuinely absent.
"""
from __future__ import annotations

import logging

from huggingface_hub import hf_hub_download
from huggingface_hub.errors import LocalEntryNotFoundError


def resolve_hf_file(
    repo_id: str,
    filename: str,
    cache_dir: str | None,
    *,
    logger: logging.Logger,
) -> str:
    """Return the local path to ``filename`` in ``repo_id``, cache-first.

    Tries ``local_files_only=True`` (no network) first. Falls back to a
    single real download only when the file isn't already in the local
    cache, logging one INFO line so a cold-cache fetch is visible.
    """
    try:
        return hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            cache_dir=cache_dir,
            local_files_only=True,
        )
    except LocalEntryNotFoundError:
        logger.info(
            "%s/%s not found in local HF cache — downloading from HuggingFace",
            repo_id, filename,
        )
        return hf_hub_download(repo_id=repo_id, filename=filename, cache_dir=cache_dir)
