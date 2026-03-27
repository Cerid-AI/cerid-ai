# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abstract LLM client contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    """Normalized LLM response."""

    content: str
    model: str
    usage: dict[str, int] | None = None


class LLMClient(ABC):
    """Abstract LLM caller — OpenRouter, Ollama, Bifrost, etc."""

    @abstractmethod
    async def call(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        breaker_name: str = "default",
    ) -> LLMResponse: ...
