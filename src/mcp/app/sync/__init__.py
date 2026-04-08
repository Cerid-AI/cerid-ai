# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cerid AI sync package — cross-machine knowledge base export/import."""

__all__ = [
    # Constants
    "MANIFEST_FILENAME", "ARTIFACTS_JSONL", "DOMAINS_JSONL",
    "RELATIONSHIPS_JSONL", "AUDIT_LOG_JSONL",
    "NEO4J_SUBDIR", "CHROMA_SUBDIR", "BM25_SUBDIR", "REDIS_SUBDIR",
    "CHROMA_BATCH_SIZE",
    # Helpers
    "_default_sync_dir", "_ensure_dir", "_sha256_file",
    "_count_jsonl_lines", "_write_jsonl", "_iter_jsonl",
    # Export
    "export_neo4j", "export_chroma", "export_bm25", "export_redis", "export_all",
    # Import
    "import_neo4j", "import_chroma", "import_bm25", "import_redis", "import_all",
    # Import helpers (ChromaDB)
    "_chroma_ensure_collection", "_chroma_get_collection_id", "_chroma_get_all_ids",
    # Manifest
    "write_manifest", "read_manifest",
    # Status
    "compare_status",
    # Tombstones
    "record_tombstone", "export_tombstones", "apply_tombstones", "purge_expired",
    # Conflicts
    "ConflictStrategy", "ConflictRecord", "detect_conflicts",
    "resolve_conflicts", "write_conflict_log",
]

from app.sync._helpers import (  # noqa: F401,E402
    ARTIFACTS_JSONL,
    AUDIT_LOG_JSONL,
    BM25_SUBDIR,
    CHROMA_BATCH_SIZE,
    CHROMA_SUBDIR,
    DOMAINS_JSONL,
    MANIFEST_FILENAME,
    NEO4J_SUBDIR,
    REDIS_SUBDIR,
    RELATIONSHIPS_JSONL,
    _count_jsonl_lines,
    _default_sync_dir,
    _ensure_dir,
    _iter_jsonl,
    _sha256_file,
    _write_jsonl,
)
from app.sync.conflicts import (  # noqa: F401,E402
    ConflictRecord,
    ConflictStrategy,
    detect_conflicts,
    resolve_conflicts,
    write_conflict_log,
)
from app.sync.export import (  # noqa: F401,E402
    export_all,
    export_bm25,
    export_chroma,
    export_neo4j,
    export_redis,
)
from app.sync.import_ import (  # noqa: F401,E402
    _chroma_ensure_collection,
    _chroma_get_all_ids,
    _chroma_get_collection_id,
    import_all,
    import_bm25,
    import_chroma,
    import_neo4j,
    import_redis,
)
from app.sync.manifest import read_manifest, write_manifest  # noqa: F401,E402
from app.sync.status import compare_status  # noqa: F401,E402
from app.sync.tombstones import (  # noqa: F401,E402
    apply_tombstones,
    export_tombstones,
    purge_expired,
    record_tombstone,
)
