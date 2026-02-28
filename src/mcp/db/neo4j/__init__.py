# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j data layer — re-exports all public functions for backward compatibility."""

__all__ = [
    # schema
    "init_schema",
    # artifacts
    "create_artifact", "find_artifact_by_filename", "update_artifact",
    "get_artifact", "list_artifacts", "recategorize_artifact",
    # relationships
    "create_relationship", "find_related_artifacts",
    "discover_relationships", "_parse_keywords", "_extract_references",
    # taxonomy
    "get_taxonomy", "create_domain", "create_sub_category",
    "list_tags", "update_artifact_taxonomy",
]

from db.neo4j.artifacts import (  # noqa: F401,E402
    create_artifact,
    find_artifact_by_filename,
    get_artifact,
    list_artifacts,
    recategorize_artifact,
    update_artifact,
)
from db.neo4j.relationships import (  # noqa: F401,E402
    _extract_references,
    _parse_keywords,
    create_relationship,
    discover_relationships,
    find_related_artifacts,
)
from db.neo4j.schema import init_schema  # noqa: F401,E402
from db.neo4j.taxonomy import (  # noqa: F401,E402
    create_domain,
    create_sub_category,
    get_taxonomy,
    list_tags,
    update_artifact_taxonomy,
)
