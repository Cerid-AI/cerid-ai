"""CLI: python -m scripts.migrate_chunk_ids [--dry-run]

Re-keys chunks still stored under positional ids. The server also runs this at
boot and after every sync import; the CLI is for an operator who wants the
counts or a dry run first.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.services.chunk_id_migration import migrate_chunk_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="count positional rows without moving them")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    counts = migrate_chunk_ids(dry_run=args.dry_run)
    print(json.dumps(counts, sort_keys=True))
    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
