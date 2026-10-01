# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""POST /upload enforces its size limit before the body is held in memory."""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

import config
from app.routers import upload as upload_module
from app.routers.upload import upload_file_endpoint

_LIMIT = 1024
_PAYLOAD = b"x" * (_LIMIT * 64)


class _RecordingFile(io.BytesIO):
    """A file that records how much each read asked for and returned."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.requested: list[int | None] = []
        self.returned = 0

    def read(self, size: int | None = -1) -> bytes:
        self.requested.append(size)
        data = super().read(size)
        self.returned += len(data)
        return data


@pytest.fixture(autouse=True)
def _small_limit(monkeypatch):
    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", _LIMIT)
    monkeypatch.setattr(config, "SUPPORTED_EXTENSIONS", {".txt"}, raising=False)


def _post(upload: UploadFile) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(upload_file_endpoint(file=upload))
    return exc_info.value


def test_oversize_part_with_known_size_is_refused_without_reading():
    backing = _RecordingFile(_PAYLOAD)
    exc = _post(UploadFile(file=backing, filename="big.txt", size=len(_PAYLOAD)))

    assert exc.status_code == 413
    assert "too large" in exc.detail.lower()
    assert backing.returned == 0


def test_oversize_part_with_unknown_size_is_read_only_up_to_the_limit():
    backing = _RecordingFile(_PAYLOAD)
    exc = _post(UploadFile(file=backing, filename="big.txt"))

    assert exc.status_code == 413
    assert "too large" in exc.detail.lower()
    assert backing.returned <= _LIMIT + 1
    assert all(size is not None and 0 < size <= _LIMIT + 1 for size in backing.requested)
