"""Live entity-extraction recall check against the annotated fixtures.

Runs inside the MCP container (``python -m tests.eval.entity_recall_runner``)
where the production extractor and the configured local model are reachable;
the pytest wrapper in ``test_entity_extraction_recall.py`` shares this logic.
"""

import asyncio
import json
import pathlib
import sys

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
RECALL_FLOOR = 0.8
MAX_CHARS = 8000


def annotation_files() -> list[pathlib.Path]:
    return sorted((FIXTURES / "entities").glob("*.json"))


def score(names: set[str], spec: dict) -> tuple[float, list[str]]:
    """Recall of ``spec['expected']`` over extracted ``names`` (lower-cased),
    matching when either string contains the other, plus the forbidden hits."""
    hits = sum(
        1 for want in spec["expected"]
        if any(want.lower() in n or n in want.lower() for n in names)
    )
    forbidden = [f for f in spec["forbidden"] if f.lower() in names]
    return hits / len(spec["expected"]), forbidden


async def run_one(annot: pathlib.Path) -> tuple[str, float, list[str], set[str]]:
    from core.agents.entity_extraction import default_llm_caller, extract_entities_from_text

    spec = json.loads(annot.read_text())
    text = (FIXTURES / spec["fixture"]).read_text()[:MAX_CHARS]
    entities = await extract_entities_from_text(text, llm_caller=default_llm_caller)
    names = {e.name.lower() for e in entities}
    recall, forbidden = score(names, spec)
    return spec["fixture"], recall, forbidden, names


async def main() -> int:
    ok = True
    for annot in annotation_files():
        fixture, recall, forbidden, names = await run_one(annot)
        passed = recall >= RECALL_FLOOR and not forbidden
        ok = ok and passed
        print(
            f"recall[{fixture}] = {recall:.2f} forbidden_hits={forbidden} "
            f"[{'PASS' if passed else 'FAIL'}]",
            flush=True,
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
