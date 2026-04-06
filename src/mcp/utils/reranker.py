import logging
import sys

from core.retrieval.reranker import *  # noqa: F401,F403
from core.retrieval.reranker import (
    _load_model,  # noqa: F401
    _session,  # noqa: F401
)
from core.retrieval.reranker import (
    warmup as _core_warmup,  # noqa: F401
)

_logger = logging.getLogger("ai-companion.reranker")


def warmup() -> None:  # type: ignore[no-redef]  # noqa: F811
    """Pre-load the reranker model (bridge-local so patches on this module work)."""
    _mod = sys.modules[__name__]
    if getattr(_mod, "_session", None) is not None:
        return
    try:
        _mod._load_model()
    except Exception:  # noqa: BLE001
        _logger.warning("Reranker warmup failed — model will be loaded on first query")
