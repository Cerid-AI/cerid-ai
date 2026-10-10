"""CLI: python -m scripts.migrate_lineages [--dry-run]

Gives memories and facts written before lineages existed their lineages. The
server also runs this at boot and after every sync import; the CLI is for an
operator who wants the counts or a dry run first.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.services.lineage_migration import migrate_lineages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="count what would change without changing it")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(migrate_lineages(dry_run=args.dry_run), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
