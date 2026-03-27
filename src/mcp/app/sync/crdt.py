# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CRDT primitives for real-time collaborative memory sync.

Provides Last-Writer-Wins Register, Observed-Remove Set, and LWW Element Dict
for conflict-free replicated data types used in multi-client sync.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any


class LWWRegister:
    """Last-Writer-Wins Register — resolves concurrent writes by timestamp."""

    __slots__ = ("value", "timestamp")

    def __init__(self, value: Any = None, timestamp: float = 0.0) -> None:
        self.value = value
        self.timestamp = timestamp

    def set(self, value: Any, timestamp: float) -> None:
        """Update the register if *timestamp* is newer than the current one."""
        if timestamp > self.timestamp:
            self.value = value
            self.timestamp = timestamp

    def merge(self, other: LWWRegister) -> LWWRegister:
        """Return a new register containing the latest write."""
        if other.timestamp > self.timestamp:
            return LWWRegister(other.value, other.timestamp)
        return LWWRegister(self.value, self.timestamp)

    def to_dict(self) -> dict:
        return {"value": self.value, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, data: dict) -> LWWRegister:
        return cls(value=data["value"], timestamp=data["timestamp"])


class ORSet:
    """Observed-Remove Set — supports concurrent add/remove without conflicts.

    Each element is tracked with unique tags so that a remove only affects
    the copies that the removing replica has observed.
    """

    def __init__(self) -> None:
        # Mapping of element -> set of unique tags
        self._entries: dict[str, set[str]] = {}

    @property
    def elements(self) -> set[str]:
        """Return the set of currently present elements."""
        return {elem for elem, tags in self._entries.items() if tags}

    def add(self, elem: str, unique_tag: str | None = None) -> str:
        """Add *elem* with a unique tag. Returns the tag used."""
        tag = unique_tag or uuid.uuid4().hex
        self._entries.setdefault(elem, set()).add(tag)
        return tag

    def remove(self, elem: str, observed_tags: set[str] | None = None) -> None:
        """Remove *elem* by discarding *observed_tags*.

        If *observed_tags* is ``None``, all current tags are removed (local remove).
        """
        if elem not in self._entries:
            return
        if observed_tags is None:
            self._entries[elem] = set()
        else:
            self._entries[elem] -= observed_tags

    def merge(self, other: ORSet) -> ORSet:
        """Return a new ORSet that is the union of both sets' tag-maps."""
        result = ORSet()
        all_keys = set(self._entries) | set(other._entries)
        for key in all_keys:
            result._entries[key] = (
                self._entries.get(key, set()) | other._entries.get(key, set())
            )
        return result

    def to_dict(self) -> dict:
        return {elem: sorted(tags) for elem, tags in self._entries.items()}

    @classmethod
    def from_dict(cls, data: dict) -> ORSet:
        obj = cls()
        for elem, tags in data.items():
            obj._entries[elem] = set(tags)
        return obj


class LWWElementDict:
    """Last-Writer-Wins Element Dictionary — a map of string keys to LWW registers."""

    def __init__(self) -> None:
        self.data: dict[str, LWWRegister] = {}

    def set(self, key: str, value: Any, timestamp: float) -> None:
        """Set *key* to *value* at *timestamp*, keeping the latest write."""
        if key in self.data:
            self.data[key].set(value, timestamp)
        else:
            self.data[key] = LWWRegister(value, timestamp)

    def get(self, key: str) -> Any | None:
        """Return the current value for *key*, or ``None``."""
        reg = self.data.get(key)
        return reg.value if reg else None

    def merge(self, other: LWWElementDict) -> LWWElementDict:
        """Return a new dict that is the merge of both dicts."""
        result = LWWElementDict()
        all_keys = set(self.data) | set(other.data)
        for key in all_keys:
            mine = self.data.get(key)
            theirs = other.data.get(key)
            if mine and theirs:
                result.data[key] = mine.merge(theirs)
            elif mine:
                result.data[key] = LWWRegister(mine.value, mine.timestamp)
            elif theirs:
                result.data[key] = LWWRegister(theirs.value, theirs.timestamp)
        return result

    def to_dict(self) -> dict:
        return {k: v.to_dict() for k, v in self.data.items()}

    @classmethod
    def from_dict(cls, data: dict) -> LWWElementDict:
        obj = cls()
        for k, v in data.items():
            obj.data[k] = LWWRegister.from_dict(v)
        return obj


@dataclass
class CRDTState:
    """Wrapper holding all CRDT primitives for a single artifact."""

    metadata: LWWElementDict = field(default_factory=LWWElementDict)
    tags: ORSet = field(default_factory=ORSet)
    content: LWWRegister = field(default_factory=LWWRegister)


def encode_delta(state: CRDTState) -> dict:
    """Serialize a ``CRDTState`` to a JSON-compatible dict."""
    return {
        "metadata": state.metadata.to_dict(),
        "tags": state.tags.to_dict(),
        "content": state.content.to_dict(),
    }


def decode_delta(data: dict) -> CRDTState:
    """Deserialize a JSON-compatible dict into a ``CRDTState``."""
    return CRDTState(
        metadata=LWWElementDict.from_dict(data.get("metadata", {})),
        tags=ORSet.from_dict(data.get("tags", {})),
        content=LWWRegister.from_dict(data.get("content", {"value": None, "timestamp": 0.0})),
    )
