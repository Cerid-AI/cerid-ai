"""`pkb_graph_neighbors` must not interpolate attacker text into Cypher.

Regression guard for the 2026-07-29 GA audit finding S2, which was filed
SUSPECTED and confirmed live on 2026-08-05: `relationship_types` was
f-string-interpolated straight into a variable-length path pattern with no
validation, while `$id` and `$limit` beside it were correctly parameterized.

Cypher has no parameter form for relationship TYPES, so that one fragment must
be interpolated — which is exactly why it needs a grammar check. A value like
``KIN]-() DETACH DELETE start //`` closes the pattern and executes arbitrary
Cypher in the same session, reachable over `/mcp/*`.

These assert the REFUSAL, not the query: the validation runs before any driver
call, so no Neo4j is required.
"""

from __future__ import annotations

import pytest

pytest.importorskip("app.mcp_tools.graph_tools")

from app.mcp_tools.graph_tools import (  # noqa: E402
    _RELATIONSHIP_TYPE_RE,
    pkb_graph_neighbors,
)

INJECTIONS = [
    "KIN]-() DETACH DELETE start //",
    "A|B]->(x) MATCH (n) DETACH DELETE n //",
    "REL` OR 1=1",
    "has space",
    "semi;colon",
    "*",
    "",
    "1STARTS_WITH_DIGIT",
]


@pytest.mark.parametrize("payload", INJECTIONS)
@pytest.mark.asyncio
async def test_injection_payloads_are_refused(payload):
    """Anything that is not a bare identifier must raise before the driver runs."""
    with pytest.raises(ValueError, match="invalid relationship type"):
        await pkb_graph_neighbors("artifact-1", relationship_types=[payload])


@pytest.mark.parametrize("payload", INJECTIONS)
def test_regex_itself_rejects_each_payload(payload):
    """The grammar, pinned independently of the call path."""
    assert not _RELATIONSHIP_TYPE_RE.fullmatch(payload)


@pytest.mark.parametrize("ok", ["MENTIONS", "RELATES_TO", "_private", "Kin2"])
def test_legitimate_types_pass_the_grammar(ok):
    """The guard must not break real callers — this is the half that proves it."""
    assert _RELATIONSHIP_TYPE_RE.fullmatch(ok)
