"""CLI: python -m scripts.run_migrations"""
from __future__ import annotations

import importlib
import logging
import sys

from app.deps import get_neo4j

MIGRATIONS = [
    "app.db.neo4j.migrations.m0001_backfill_verification_edges",
    "app.db.neo4j.migrations.m0002_cleanup_orphan_verification_reports",
]


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    driver = get_neo4j()
    for name in MIGRATIONS:
        mod = importlib.import_module(name)
        stats = mod.run(driver)
        print(f"{name}: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
