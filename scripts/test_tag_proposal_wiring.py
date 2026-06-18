#!/usr/bin/env python3
"""Mock-based test of write_knowledge tag-governance wiring (ADR-012).

No live DB — get_db_conn and embedding_request are stubbed. Confirms:
  1. unknown tags are recorded into tag_proposals (UPSERT) and NOT written to
     knowledge.tags; only canonical (+ shape) tags reach the INSERT.
  2. casing/spacing variants dedup within one call.
  3. fail-soft: if the tag_proposals INSERT raises, ingest still succeeds.
  4. vocabulary fallback: if tag_vocabulary is unreachable, the static
     CANONICAL_TAGS seed is used.

Run: .venv/bin/python scripts/test_tag_proposal_wiring.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import knowledge_ingest as ki  # noqa: E402


class FakeCursor:
    def __init__(self, ret):
        self._ret = ret

    def fetchone(self):
        return self._ret

    def fetchall(self):
        return self._ret if isinstance(self._ret, list) else []


class FakeTxn:
    """Stand-in for psycopg's conn.transaction() (SAVEPOINT)."""

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        return False  # let exceptions propagate to the caller's try/except


class FakeConn:
    def __init__(self, *, vocab, proposals_fail=False):
        self.vocab = vocab
        self.proposals_fail = proposals_fail
        self.executed: list[tuple] = []
        self.proposal_upserts: list[list] = []
        self.knowledge_insert: list | None = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def transaction(self):
        return FakeTxn()

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        s = " ".join(sql.split())
        if "FROM public.tag_vocabulary" in s and "SELECT tag" in s:
            return FakeCursor([{"tag": t} for t in self.vocab])
        if "INSERT INTO public.tag_proposals" in s:
            if self.proposals_fail:
                raise RuntimeError('relation "public.tag_proposals" does not exist')
            self.proposal_upserts.append(params)
            return FakeCursor(None)
        if "INSERT INTO public.knowledge" in s:
            self.knowledge_insert = params
            return FakeCursor({"id": "00000000-0000-0000-0000-000000000001"})
        return FakeCursor(None)


class NoVocabConn(FakeConn):
    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM public.tag_vocabulary" in s and "SELECT tag" in s:
            raise RuntimeError("no such table")
        return super().execute(sql, params)


VOCAB = ["K8s", "Network", "Production", "OpenBrain"]
FAILS = 0


def run(name, cond):
    global FAILS
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILS += 1


def main() -> int:
    global FAILS
    ki.embedding_request = lambda text: None  # skip network

    # Test 1: unknown queued, only canonical written.
    conn = FakeConn(vocab=VOCAB)
    ki.get_db_conn = lambda: conn
    res = ki.write_knowledge(
        "some content body", "mmcmahon",
        domain="Network", environment="Production",
        tags=["kubernetes", "Network", "totally-new-tag"],
        source_type="text",
    )
    run("1a result accepted", res["status"] == "accepted")
    written = conn.knowledge_insert[5]  # tags is param index 5
    run("1b canonical K8s written (alias fold)", "K8s" in written)
    run("1c canonical Network written", "Network" in written)
    run("1d unknown NOT in knowledge.tags", "totally-new-tag" not in written)
    run("1e shape tag present", "shape:note" in written)
    keys = [p[0] for p in conn.proposal_upserts]
    run("1f unknown queued as proposal", "totally-new-tag" in keys)
    run("1g known/aliased NOT queued", "kubernetes" not in keys and "network" not in keys)
    run("1h proposal sample_context set", conn.proposal_upserts[0][3] == "some content body")
    run("1i returned tags == written tags", res["tags"] == written)

    # Test 2: casing dedup within one call.
    conn2 = FakeConn(vocab=VOCAB)
    ki.get_db_conn = lambda: conn2
    ki.write_knowledge(
        "c2", "annie", domain="Network", environment="Production",
        tags=["FooBar", "foobar", "Baz"], source_type="text",
    )
    keys2 = sorted(p[0] for p in conn2.proposal_upserts)
    run("2a foobar deduped to one row", keys2.count("foobar") == 1)
    run("2b two distinct unknowns queued", keys2 == ["baz", "foobar"])

    # Test 3: fail-soft when tag_proposals missing.
    conn3 = FakeConn(vocab=VOCAB, proposals_fail=True)
    ki.get_db_conn = lambda: conn3
    res3 = ki.write_knowledge(
        "c3", "mmcmahon", domain="Network", environment="Production",
        tags=["unknownish", "K8s"], source_type="text",
    )
    run("3a ingest accepted despite proposals failure", res3["status"] == "accepted")
    run("3b knowledge insert still happened", conn3.knowledge_insert is not None)
    run("3c canonical still applied", "K8s" in conn3.knowledge_insert[5])
    run("3d unknown excluded from knowledge.tags", "unknownish" not in conn3.knowledge_insert[5])

    # Test 4: vocabulary fallback to static CANONICAL_TAGS seed.
    conn4 = NoVocabConn(vocab=[])
    ki.get_db_conn = lambda: conn4
    ki.write_knowledge(
        "c4", "mmcmahon", domain="Network", environment="Production",
        tags=["K8s", "novel-tag"], source_type="text",
    )
    run("4a fallback keeps seed canonical K8s", "K8s" in conn4.knowledge_insert[5])
    run("4b novel still queued", "novel-tag" in [p[0] for p in conn4.proposal_upserts])

    print()
    print("FAILURES:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
