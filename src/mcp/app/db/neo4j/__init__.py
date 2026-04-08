# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j data layer — re-exports all public functions for backward compatibility."""

__all__ = [
    # schema
    "init_schema",
    # artifacts
    "create_artifact", "delete_artifact", "find_artifact_by_filename",
    "update_artifact", "get_artifact", "get_active_memories", "get_quality_scores",
    "get_verification_report", "save_verification_report",
    "list_artifacts", "recategorize_artifact", "update_artifact_summary",
    # relationships
    "create_relationship", "find_related_artifacts",
    "discover_relationships", "_parse_keywords", "_extract_references",
    # taxonomy
    "get_taxonomy", "create_domain", "create_sub_category",
    "list_tags", "update_artifact_taxonomy",
    # memory (Phase 44 Part 2)
    "ensure_memory_schema", "create_memory_node", "update_memory_access",
    "archive_memory", "link_memory_to_artifact", "supersede_memory",
    "merge_memory", "get_memory_graph",
]

from app.db.neo4j.artifacts import (  # noqa: F401,E402
    create_artifact,
    delete_artifact,
    find_artifact_by_filename,
    get_active_memories,
    get_artifact,
    get_quality_scores,
    get_verification_report,
    list_artifacts,
    recategorize_artifact,
    save_verification_report,
    update_artifact,
    update_artifact_summary,
)
from app.db.neo4j.memory import (  # noqa: F401,E402
    archive_memory,
    create_memory_node,
    ensure_memory_schema,
    get_memory_graph,
    link_memory_to_artifact,
    merge_memory,
    supersede_memory,
    update_memory_access,
)
from app.db.neo4j.relationships import (  # noqa: F401,E402
    _extract_references,
    _parse_keywords,
    create_relationship,
    discover_relationships,
    find_related_artifacts,
)
from app.db.neo4j.schema import init_schema  # noqa: F401,E402
from app.db.neo4j.taxonomy import (  # noqa: F401,E402
    create_domain,
    create_sub_category,
    get_taxonomy,
    list_tags,
    update_artifact_taxonomy,
)
