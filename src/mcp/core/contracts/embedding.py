# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Abstract embedding function contract."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingFunction(ABC):
    """Abstract embedding — ONNX, OpenAI, Cohere, etc."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...
