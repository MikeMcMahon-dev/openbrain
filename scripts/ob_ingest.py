#!/usr/bin/env python3
"""ob_ingest.py — ingest a note DIRECTLY into OpenBrain, bypassing the claude.ai
MCP connector edge.

WHY: the MCP path (mcp__claude_ai_OpenBrain__ingest) traverses Anthropic's
connector edge, whose Cloudflare WAF blocks request bodies containing
shell-command / system-path tokens (sudo, /etc/..., unix://..., etc.) as a
command-injection false positive — a bad fit for an SRE knowledge vault. This
hits OpenBrain's own Vercel API instead, so exact commands are preserved verbatim.
Proven 2026-07-19: identical command-heavy body -> WAF-blocked via MCP, HTTP 200
via this direct path.

TOKEN (never hardcoded): read at runtime from $OPENBRAIN_TOOL_ACCESS_TOKEN, else
the OPENBRAIN_TOOL_ACCESS_TOKEN line in <repo>/.env.local (gitignored).

USAGE:
  echo "note text" | python scripts/ob_ingest.py --subject session-context --topic session-wrap
  python scripts/ob_ingest.py --file wrap.md --subject session-context --topic session-wrap \
      --domain Network --environment Production --tags SpectreNet,Ops

LIVING DOCS vs EVENT NOTES (ADR-008):
  A "living" current-state doc (topology, inventory, config-of-record) should carry a
  --component IDENTITY so a re-ingest REPLACES the prior current row in place instead of
  piling up a second competing 'current' note. Everything else (session wraps, incident
  reports) omits --component and is append-only history.

    # living doc — replaces the prior SpectreNet DNS current-state row:
    python scripts/ob_ingest.py --file dns-state.md --subject spectrenet-dns \
        --domain Network --environment Production --component dns-current-state

PLAN/APPLY (ADR ingest airlock):
  Every ingest now plans FIRST, automatically. The plan is read-only; it returns the living docs
  already in scope plus a short-lived `plan_token` bound to this exact content, and the token is
  folded into the ingest so the write is provably the thing that was planned.

  When living docs are in scope you must say which this is. There is no default:
    --component <name>          this UPDATES that living doc (supersedes it in place)
    --ack-not-updating          this is an append-only note; declines every candidate BY NAME
    --decline-reason "<why>"    additionally required when a candidate scores at or above the
                                plan's decline_reason_threshold

  The script deliberately does NOT auto-fill the decline list from the plan it just fetched —
  that would be rubber-stamping its own output. It stops with exit code 2 and prints the plan.

  --plan alone previews and exits, writing nothing. --no-plan is an escape hatch for when
  /api/plan_ingest is unreachable; it sends no token and will be rejected once
  OPENBRAIN_REQUIRE_INGEST_PLAN is on.

  The vault can change inside the token's TTL, so the server re-derives candidates at apply time.
  If one appeared since the plan, the apply 409s naming it — re-run and decide again.

  --component X  adds the tag `component:X` (ADR-008 identity key). The (system, component:X)
  pair is the supersession identity, so `--system` is REQUIRED with --component — a null
  system is what made the identity unsatisfiable on purpose (ADR-018 P2 / ADR-019).
  --no-supersede disables the auto-retire (append even if a current row exists — rarely wanted).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = os.getenv("OPENBRAIN_API_BASE", "https://openbrain-rouge.vercel.app")


def load_token(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    env = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN")
    if env:
        return env.strip()
    # script lives in <repo>/scripts/ ; token is in <repo>/.env.local (gitignored)
    env_local = Path(__file__).resolve().parent.parent / ".env.local"
    if env_local.is_file():
        for line in env_local.read_text().splitlines():
            line = line.strip()
            if line.startswith("OPENBRAIN_TOOL_ACCESS_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def fetch_plan(base: str, token: str, body: str,
               system: str | None, component: str | None) -> dict:
    """POST the plan. Read-only on the server — writes nothing, returns current state plus a
    short-lived token bound to this exact content."""
    req = urllib.request.Request(
        base.rstrip("/") + "/api/plan_ingest",
        data=json.dumps({"source": body, "system": system, "component": component}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        sys.exit(f"plan failed: HTTP {exc.code} {exc.read().decode()[:400]}")
    except urllib.error.URLError as exc:
        sys.exit(f"plan failed: {exc}")


def print_plan(plan: dict, system: str | None) -> None:
    state = plan.get("current_state", {})
    living = state.get("living_docs_in_system") or []
    print(f"Living docs in system={system or '(none declared)'}:")
    for d in living:
        print(f"  - {d['component_key']:<34} ({d['age_days']}d)  {str(d['title'])[:44]!r}")
    if not living:
        print("  (none)")
    similar = state.get("similar_living_docs") or []
    if similar:
        print("\nSimilar living docs (suggestion only — similarity cannot tell an update "
              "from a note):")
        for d in similar:
            print(f"  - {d['similarity']:.3f}  {d['component_key']}")
    ws = plan.get("would_supersede")
    print(f"\nWould supersede: {ws['id']} ({ws['component_key']})" if ws
          else "\nWould supersede: nothing (this would be a NEW row)")


def close_matches(plan: dict) -> tuple[list[dict], bool]:
    """(candidates whose decline costs a written reason, whether the server declared the bar).

    The threshold is read FROM the plan rather than copied here, so this script never carries a
    second, drifting copy of a tuned constant. If the server is too old to send one we CANNOT
    evaluate the rule — and a gate that cannot evaluate its own rule must fail CLOSED, not wave
    the write through. (It failed open once, against a server that predated the field, and a
    0.777 near-duplicate went straight into the vault unchallenged.) With no bar declared, every
    suggested doc counts as close; they are already above the server's 0.50 suggestion floor, so
    the set stays bounded.
    """
    similar = (plan.get("current_state") or {}).get("similar_living_docs") or []
    threshold = plan.get("decline_reason_threshold")
    if threshold is None:
        return similar, False
    return [d for d in similar if (d.get("similarity") or 0) >= threshold], True


def main() -> None:
    p = argparse.ArgumentParser(description="Direct OpenBrain ingest (bypasses the MCP-edge WAF).")
    p.add_argument("--file", help="file containing the note body; default: stdin")
    p.add_argument("--source-type", default="text", choices=["text", "url"])
    p.add_argument("--subject", default="session-context")
    p.add_argument("--topic", default="session-wrap")
    p.add_argument("--domain", help="Network|K8s|Security|Study|OpenBrain|Personal")
    p.add_argument("--environment", help="Production|Lab|Study|Archive")
    p.add_argument("--system", help="namespace (SpectreNet|PMX-01|OpenBrain|Annie|...); "
                                    "REQUIRED with --component (the supersession pivot)")
    p.add_argument("--tags", help="comma-separated tags")
    p.add_argument("--component",
                   help="ADR-008 living-doc identity: adds tag component:<val> so a re-ingest "
                        "replaces the prior current row for this (system, component) in place")
    p.add_argument("--no-supersede", dest="supersede", action="store_false",
                   help="disable living-doc auto-supersede (append even if a current row exists)")
    p.set_defaults(supersede=True)
    p.add_argument("--valid-from", dest="valid_from",
                   help="ADR-018 P5 fact-onset (valid-time), ISO-8601 e.g. 2026-07-15 — set it "
                        "when the fact predates ingest; default is now(). Also becomes the "
                        "retired predecessor's fact-offset (contiguous lifespans).")
    p.add_argument("--plan", action="store_true",
                   help="PREVIEW only: show the living docs already in scope and what a commit "
                        "would supersede, then exit. Writes nothing. Run before any living-doc "
                        "ingest — it is the only way to see what exists before deciding whether "
                        "this is an update.")
    p.add_argument("--ack-not-updating", dest="ack_not_updating", action="store_true",
                   help="declare that this is an append-only note and NOT an update to any of "
                        "the living docs the plan surfaced. Acknowledges every candidate by "
                        "name. Required on a --component-less ingest when living docs are in "
                        "scope — the answer is never assumed on your behalf.")
    p.add_argument("--decline-reason", dest="decline_reason",
                   help="why this is a new record rather than an update. Required with "
                        "--ack-not-updating when a candidate scores at or above the plan's "
                        "decline_reason_threshold.")
    p.add_argument("--no-plan", dest="do_plan", action="store_false",
                   help="ESCAPE HATCH: skip the automatic plan round-trip. Only for when "
                        "/api/plan_ingest is unreachable and a wrap still has to land. This "
                        "sends no plan_token, so it will be REJECTED once "
                        "OPENBRAIN_REQUIRE_INGEST_PLAN is on.")
    p.set_defaults(do_plan=True)
    p.add_argument("--endpoint", default="/api/ingest")
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--token", help="override; else $OPENBRAIN_TOOL_ACCESS_TOKEN or .env.local")
    args = p.parse_args()

    body = Path(args.file).read_text() if args.file else sys.stdin.read()
    if not body.strip():
        sys.exit("error: empty source body")

    token = load_token(args.token)
    if not token:
        sys.exit("error: no token — set OPENBRAIN_TOOL_ACCESS_TOKEN or add it to .env.local")

    payload = {
        "source_type": args.source_type,
        "source": body,
        "subject": args.subject,
        "topic": args.topic,
    }
    if args.domain:
        payload["domain"] = args.domain
    if args.environment:
        payload["environment"] = args.environment
    if args.system:
        payload["system"] = args.system
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    if args.component:
        comp = args.component.strip()
        comp_tag = comp if comp.startswith("component:") else f"component:{comp}"
        if comp_tag not in tags:
            tags.append(comp_tag)
    if tags:
        payload["tags"] = tags
    if not args.supersede:
        payload["auto_supersede"] = False
    if args.valid_from:
        payload["valid_from"] = args.valid_from

    # Plan first, always. The plan is read-only, so this costs one round-trip and buys the
    # token that proves this exact content was planned. --plan stops here; otherwise the token
    # rides along on the ingest.
    plan = None
    if args.do_plan:
        plan = fetch_plan(args.base, token, body, args.system, args.component)

    if args.plan:
        if plan is None:
            sys.exit("error: --plan and --no-plan are contradictory")
        print("PLAN — nothing written.\n")
        print_plan(plan, args.system)
        print(f"\n{plan.get('decision_required', '')}")
        print(f"\nplan_token (valid {plan.get('expires_in')}s):\n{plan.get('plan_token')}")
        return

    if plan is not None:
        payload["plan_token"] = plan["plan_token"]

        # The decision the gate exists to force. --component IS the decision ("update that one"),
        # so it needs nothing further. Without one, every candidate the plan surfaced has to be
        # declined BY NAME. Auto-filling that list from the plan's own output would make this
        # script rubber-stamp its own plan, which is the exact failure the airlock is for.
        candidates = plan.get("candidates") or []
        if not args.component and candidates:
            if not args.ack_not_updating:
                print("STOPPED — living docs are in scope and no decision was declared. "
                      "Nothing written.\n")
                print_plan(plan, args.system)
                print("\nRe-run with ONE of:")
                print("  --component <name>        update that living doc in place "
                      "(--system is required with it)")
                print("  --ack-not-updating        write an append-only note, declining all "
                      f"{len(candidates)} candidate(s) above")
                sys.exit(2)

            payload["acknowledged_not_updating"] = candidates
            close, threshold_known = close_matches(plan)
            reason = (args.decline_reason or "").strip()
            if close and not reason:
                print("STOPPED — declining a close match costs a written reason. "
                      "Nothing written.\n")
                print_plan(plan, args.system)
                top = close[0]
                if threshold_known:
                    print(f"\n{top['component_key']} scores {top['similarity']:.3f}, at or above "
                          f"the threshold of {plan['decline_reason_threshold']}.")
                else:
                    print(f"\nThis server did not declare decline_reason_threshold, so the bar "
                          f"cannot be checked here and every suggested doc is treated as close. "
                          f"Closest: {top['component_key']} at {top['similarity']:.3f}.")
                print("Re-run with --decline-reason \"<why this is a new record, not an "
                      "update to it>\"")
                sys.exit(2)
            if reason:
                payload["decline_reason"] = reason

    req = urllib.request.Request(
        args.base.rstrip("/") + args.endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            print(raw)
            # The JSON above echoes the whole document back, so a one-line note buried in
            # `details` is not something anyone reads. Tags queued for review are exactly
            # the thing that gets missed - the write succeeds, the note looks tagged, and
            # the tags are actually parked in an approval queue nobody is working. Say it
            # again, on its own, after the wall of JSON.
            try:
                queued = [d for d in (json.loads(raw).get("details") or [])
                          if "queued for review" in str(d)]
            except Exception:
                queued = []
            for line in queued:
                print(f"\n!! {line}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        if "Cloudflare" in body or "you have been blocked" in body:
            sys.exit(f"error: HTTP {e.code} — WAF block (unexpected on the direct path)\n{body}")
        sys.exit(f"error: HTTP {e.code}\n{body}")
    except urllib.error.URLError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
