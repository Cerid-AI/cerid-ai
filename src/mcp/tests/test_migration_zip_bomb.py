# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Zip-bomb guard applies to migration endpoints too."""
import zipfile

import pytest
from fastapi import HTTPException

from app.parsers._zip_safety import assert_safe_zip


def test_migration_zip_bomb_guard_active(tmp_path):
    """The same guard used by office/epub parsers covers migration imports."""
    # Build a normal zip and prove the guard rejects it when the cap is tiny
    path = tmp_path / "migration.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("doc1.md", b"A" * 1000)
        zf.writestr("doc2.md", b"B" * 1000)

    # Sanity: passes under default cap
    assert_safe_zip(str(path))

    # Rejects under lowered cap
    import app.parsers._zip_safety as zs
    original = zs.MAX_UNCOMPRESSED_BYTES
    zs.MAX_UNCOMPRESSED_BYTES = 500
    try:
        with pytest.raises(HTTPException) as exc_info:
            assert_safe_zip(str(path))
        assert exc_info.value.status_code == 413
    finally:
        zs.MAX_UNCOMPRESSED_BYTES = original
