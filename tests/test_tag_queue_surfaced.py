"""Tags queued for review (ADR-012) must be NAMED in the ingest response.

Queuing an unknown tag instead of writing or dropping it is correct. Saying nothing about
it was not: the response reports honored-vs-inferred mismatches for domain, environment and
system, and was silent about tags. A note ingested with four good tags came back carrying
only `shape:note`, with no explanation anywhere, and that was diagnosed as "the API dropped
my tags" — the tags were in tag_proposals the whole time, and the queue had 42 unworked
entries nobody had been told about.

These prove the warning appears when tags are queued, and — the half that matters — that it
does NOT appear when nothing was queued. A notice that fires unconditionally is noise, and a
test built only from the positive case cannot tell the two apart.

Fully mocked — no DB, no embeddings.

Run: cd tests && python -m pytest test_tag_queue_surfaced.py -q
"""
from __future__ import annotations

from unittest.mock import patch

from api import _openbrain_api as ob


def _tax(**_kw):
    return {"domain": "Network", "environment": "Production",
            "system": "SpectreNet", "tags": [], "shape": "note"}


def _writer(queued):
    """Stand in for write_knowledge, returning whatever the tag governor would have queued."""
    def _fn(_content, _owner, **_kw):
        result = {"status": "accepted"}
        if queued is not None:
            result["tags_queued_for_review"] = list(queued)
        return result
    return _fn


def _run(queued, warnings):
    with patch("api.knowledge_ingest.write_knowledge", side_effect=_writer(queued)), \
         patch("api.taxonomy_map.map_to_taxonomy", side_effect=_tax), \
         patch.object(ob, "_honor_owners", return_value={"mike.mcmahon67"}):
        return ob._write_text_ingest_knowledge(
            "some content", "mike.mcmahon67", "subj", "topic",
            system_override="SpectreNet", warnings=warnings)


def test_queued_tags_are_named_in_the_warnings():
    warnings: list[str] = []
    assert _run(["Technitium", "MemoryArchive"], warnings) is None
    assert len(warnings) == 1
    msg = warnings[0]
    # The names themselves, not just a count — "2 tags were queued" sends nobody anywhere.
    assert "Technitium" in msg and "MemoryArchive" in msg
    assert "2 tag(s)" in msg
    # And what to do about it, since the queue is a human step that had stalled.
    assert "tag_review.py --approve" in msg


def test_no_warning_when_nothing_was_queued():
    warnings: list[str] = []
    assert _run([], warnings) is None
    assert warnings == []


def test_no_warning_when_the_writer_predates_the_field():
    """An older writer returns no key at all. That must read as 'nothing queued', not crash."""
    warnings: list[str] = []
    assert _run(None, warnings) is None
    assert warnings == []


def test_write_knowledge_declares_the_key_on_success():
    """The seam only works if write_knowledge actually returns it. Guard the contract in the
    source, since exercising the real function needs a database."""
    import inspect

    from api import knowledge_ingest

    src = inspect.getsource(knowledge_ingest.write_knowledge)
    assert '"tags_queued_for_review"' in src
    assert "unknown_tags: list[str] = []" in src   # hoisted, so the return can see it
