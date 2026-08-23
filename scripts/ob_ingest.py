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

    # --plan: read-only preview, then stop. Prints the living docs already in scope and what a
    # commit would supersede, so "is this an update?" is answered from the vault rather than from
    # memory. Nothing is written. Run this before any living-doc ingest.
    if args.plan:
        plan_req = urllib.request.Request(
            args.base.rstrip("/") + "/api/plan_ingest",
            data=json.dumps({
                "source": body,
                "system": args.system,
                "component": args.component,
            }).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(plan_req, timeout=30) as r:
                plan = json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            sys.exit(f"plan failed: HTTP {exc.code} {exc.read().decode()[:400]}")

        state = plan.get("current_state", {})
        print("PLAN — nothing written.\n")
        living = state.get("living_docs_in_system") or []
        print(f"Living docs in system={args.system or '(none declared)'}:")
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
        print(f"\n{plan.get('decision_required', '')}")
        print(f"\nplan_token (valid {plan.get('expires_in')}s):\n{plan.get('plan_token')}")
        return

    req = urllib.request.Request(
        args.base.rstrip("/") + args.endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        if "Cloudflare" in body or "you have been blocked" in body:
            sys.exit(f"error: HTTP {e.code} — WAF block (unexpected on the direct path)\n{body}")
        sys.exit(f"error: HTTP {e.code}\n{body}")
    except urllib.error.URLError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
