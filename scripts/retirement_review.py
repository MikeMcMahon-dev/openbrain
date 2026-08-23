#!/usr/bin/env python3
"""retirement_review.py — the human half of the removal airlock (migration 012).

Agents queue removals via `api/retirement_request.py`. Nothing leaves the vault until a human
runs this. Approving is not executing: `approve` records a decision, `execute` performs it, and
they are separate commands on purpose so a mis-click cannot delete anything.

    python scripts/retirement_review.py list                    # pending queue, with previews
    python scripts/retirement_review.py show <request_id>        # full target content + evidence
    python scripts/retirement_review.py approve <request_id> --note "..."
    python scripts/retirement_review.py deny    <request_id> --note "..."
    python scripts/retirement_review.py execute [<request_id>]   # approved only; asks first

EXECUTION RE-CHECKS THE EVIDENCE. The reference counts captured at request time go stale — a row
that was safe to hard-delete an hour ago may since have been referenced by a supersession event,
and that FK is not deferrable. On any drift this refuses and marks the request 'failed' rather
than pushing through. The stale-evidence case is the whole reason execution is a separate step.

  method='retire' -> appends a supersession_events expiry row (superseding_id NULL); the
                     projection sets status='historical'. Content preserved, reachable via as_of.
  method='delete' -> hard DELETE of the row and its chunks. Irreversible.

Note the ordering trap: a 'retire' FORECLOSES a later 'delete'. Once an immutable event
references the row, the non-deferrable FK pins it permanently. Retiring is the safe default and
the one-way door — pick deliberately.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV = ROOT / ".env.local"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

REVIEWER = os.getenv("OPENBRAIN_REVIEWER") or os.getenv("USER") or "mike.mcmahon67"


def _conn():
    # prepare_threshold=None: the Supabase transaction pooler (6543) does not keep prepared
    # statements across checkouts, and reusing one raises DuplicatePreparedStatement.
    return psycopg.connect(os.environ["SUPABASE_DB_URL"], row_factory=dict_row,
                           prepare_threshold=None)


def cmd_list(_args) -> int:
    with _conn() as c:
        rows = list(c.execute(
            """SELECT r.id::text, r.target_id::text, r.method, r.reason_code, r.rationale,
                      r.requested_by, r.requested_at, r.evidence,
                      split_part(regexp_replace(k.content,'^#+\\s*',''), E'\\n',1) AS title,
                      k.status AS target_status
                 FROM public.retirement_requests r
                 JOIN public.knowledge k ON k.id = r.target_id
                WHERE r.status = 'pending' ORDER BY r.requested_at"""))
    if not rows:
        print("No pending retirement requests.")
        return 0
    print(f"{len(rows)} pending:\n")
    for r in rows:
        ev = r["evidence"] or {}
        print(f"  {r['id']}")
        print(f"    target : {r['target_id'][:8]}  [{r['target_status']}]  "
              f"{str(r['title'])[:58]!r}")
        print(f"    method : {r['method']}  ({r['reason_code']})   by {r['requested_by']}"
              f"   {r['requested_at']:%Y-%m-%d %H:%M}")
        print(f"    refs   : events={ev.get('ref_events')} parents={ev.get('ref_parents')} "
              f"contradictions={ev.get('ref_contradictions')} chunks={ev.get('chunks')} "
              f"age={ev.get('age_days')}d")
        print(f"    why    : {r['rationale'][:150]}")
        print()
    return 0


def cmd_show(args) -> int:
    with _conn() as c:
        # LEFT JOIN, not JOIN: after an executed delete the target is gone, and an inner join
        # made the audit record unreadable at exactly the moment it is the only evidence the
        # removal happened. Fall back to the evidence snapshot captured at request time.
        r = c.execute(
            """SELECT r.*, k.content, k.tags, k.domain, k.environment, k.system, k.component_key
                 FROM public.retirement_requests r
                 LEFT JOIN public.knowledge k ON k.id = r.target_id
                WHERE r.id = %s""", [args.request_id]).fetchone()
    if not r:
        print(f"No such request: {args.request_id}")
        return 1
    print(f"request  : {r['id']}  [{r['status']}]")
    print(f"target   : {r['target_id']}")
    print(f"method   : {r['method']} ({r['reason_code']})   by {r['requested_by']}")
    print(f"taxonomy : {r['domain']}/{r['environment']} system={r['system']} "
          f"component={r['component_key']} tags={r['tags']}")
    print(f"rationale: {r['rationale']}")
    print(f"evidence : {json.dumps(r['evidence'], indent=2, default=str)}")
    if r["content"] is None:
        ev = r["evidence"] or {}
        print(f"\n--- target content ---\n(target row no longer exists — removed by this "
              f"request. Snapshot at request time: {ev.get('title')!r}, "
              f"{ev.get('content_len')} chars)")
    else:
        print(f"\n--- target content ---\n{r['content']}")
    return 0


def _decide(request_id: str, decision: str, note: str | None) -> int:
    with _conn() as c:
        r = c.execute("SELECT status FROM public.retirement_requests WHERE id = %s",
                      [request_id]).fetchone()
        if not r:
            print(f"No such request: {request_id}")
            return 1
        if r["status"] != "pending":
            print(f"Request is '{r['status']}', not 'pending' — nothing to decide.")
            return 1
        c.execute(
            """UPDATE public.retirement_requests
                  SET status = %s, decided_by = %s, decided_at = now(), decision_note = %s
                WHERE id = %s""", [decision, REVIEWER, note, request_id])
        c.commit()
    print(f"{request_id} -> {decision} by {REVIEWER}")
    if decision == "approved":
        print("Nothing removed yet. Run `execute` to perform it.")
    else:
        print("Denial recorded. This target+method will not be re-proposed.")
    return 0


def cmd_approve(args) -> int:
    return _decide(args.request_id, "approved", args.note)


def cmd_deny(args) -> int:
    return _decide(args.request_id, "denied", args.note)


def _recheck(c, target_id: str, method: str) -> tuple[bool, str]:
    """Re-verify at execution time. Evidence captured at request time may be stale."""
    from api.retirement_request import collect_evidence
    ev = collect_evidence(c, target_id)
    if not ev:
        return False, "target no longer exists"
    if method == "delete" and not ev["hard_delete_legal"]:
        return False, (f"hard delete no longer legal (events={ev['ref_events']} "
                       f"parents={ev['ref_parents']} contradictions={ev['ref_contradictions']})")
    if method == "retire" and ev["status"] != "current":
        return False, f"target is already '{ev['status']}' — nothing to retire"
    return True, "ok"


def cmd_execute(args) -> int:
    with _conn() as c:
        q = ("""SELECT id::text, target_id::text, method, reason_code
                  FROM public.retirement_requests WHERE status = 'approved'""")
        params: list = []
        if args.request_id:
            q += " AND id = %s"
            params.append(args.request_id)
        rows = list(c.execute(q + " ORDER BY decided_at", params))

        if not rows:
            print("No approved requests to execute.")
            return 0

        print(f"About to execute {len(rows)} approved request(s):")
        for r in rows:
            print(f"  {r['method']:<7} {r['target_id'][:8]}  ({r['reason_code']})")
        if not args.yes:
            if input("\nProceed? type 'yes' to continue: ").strip().lower() != "yes":
                print("Aborted. Nothing changed.")
                return 1

        failures = 0
        for r in rows:
            ok, why = _recheck(c, r["target_id"], r["method"])
            if not ok:
                c.execute("""UPDATE public.retirement_requests
                                SET status='failed', decision_note =
                                    coalesce(decision_note,'') || ' | execute refused: ' || %s
                              WHERE id = %s""", [why, r["id"]])
                c.commit()
                failures += 1
                print(f"  REFUSED {r['id'][:8]}: {why}")
                continue

            try:
                detail = _perform(c, r)
            except Exception as exc:
                # Any DB error here (the target FK violation was the original one) previously
                # escaped as a traceback, leaving the request 'approved' — so the next run
                # retried it and failed identically, forever. Record it and keep going.
                c.rollback()
                reason = str(exc).split("\n")[0][:180]
                c.execute("""UPDATE public.retirement_requests
                                SET status='failed', decision_note =
                                    coalesce(decision_note,'') || ' | execute failed: ' || %s
                              WHERE id = %s""", [reason, r["id"]])
                c.commit()
                failures += 1
                print(f"  FAILED  {r['id'][:8]}: {reason}")
                continue

            c.execute("""UPDATE public.retirement_requests
                            SET status='executed', executed_at=now() WHERE id = %s""", [r["id"]])
            c.commit()
            print(f"  OK      {r['id'][:8]}: {detail}")
    return 1 if failures else 0


def _perform(c, r) -> str:
    """Perform the removal. Raises on failure; the caller records it and moves on."""
    if r["method"] == "retire":
        c.execute(
            """INSERT INTO public.supersession_events
                 (superseded_id, superseding_id, reason_code, reason_note, actor, method)
               VALUES (%s, NULL, %s, %s, %s, 'human')""",
            [r["target_id"], r["reason_code"],
             f"approved retirement request {r['id']}", REVIEWER])
        return "retired (status -> historical)"

    ch = c.execute("DELETE FROM public.knowledge_chunked WHERE document_id = %s",
                   [r["target_id"]]).rowcount
    kn = c.execute("DELETE FROM public.knowledge WHERE id = %s",
                   [r["target_id"]]).rowcount
    return f"deleted ({kn} row, {ch} chunks)"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)

    s = sub.add_parser("show")
    s.add_argument("request_id")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("approve")
    s.add_argument("request_id")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("deny")
    s.add_argument("request_id")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_deny)

    s = sub.add_parser("execute")
    s.add_argument("request_id", nargs="?")
    s.add_argument("--yes", action="store_true", help="skip the interactive confirm")
    s.set_defaults(fn=cmd_execute)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
