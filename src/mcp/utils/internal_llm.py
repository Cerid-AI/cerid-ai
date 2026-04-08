# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Re-export bridge — implementation lives in core.utils.internal_llm."""

from core.utils.internal_llm import *  # noqa: F401,F403
from core.utils.internal_llm import call_internal_llm, close_ollama_client  # noqa: F401
