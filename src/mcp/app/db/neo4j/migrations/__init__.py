# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j migrations package.

Legacy one-shot migrations (``backfill_updated_at``, ``register_recategorized_at``,
``migrate_memory_salience``) are re-exported from :mod:`.legacy` so that existing
callers (``app.main``) continue to work after ``migrations.py`` was promoted to a
package to host versioned migrations like :mod:`.m0001_backfill_verification_edges`.
"""

from __future__ import annotations

from app.db.neo4j.migrations.legacy import (  # noqa: F401
    backfill_updated_at,
    migrate_memory_salience,
    register_recategorized_at,
)

__all__ = [
    "backfill_updated_at",
    "migrate_memory_salience",
    "register_recategorized_at",
]
