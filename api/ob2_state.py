from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api._openbrain_api import (
    get_db_conn,
    parse_request,
    require_auth_owner,
    response_payload,
)

VALID_DOMAINS = {"Network", "K8s", "Security", "Study", "OpenBrain", "Personal"}
VALID_ENVIRONMENTS = {"Production", "Lab", "Study", "Archive"}
VALID_STATUSES = {"current", "superseded", "historical", "draft"}


def _mark_wiki_pages_stale(conn: Any, domain: str, system: str | None) -> None:
    try:
        if system:
            conn.execute(
                "UPDATE public.wiki_pages SET is_stale = true WHERE domain = %s AND system = %s",
                [domain, system],
            )
        else:
            conn.execute(
                "UPDATE public.wiki_pages SET is_stale = true WHERE domain = %s AND system IS NULL",
                [domain],
            )
    except Exception:
        pass  # stale marking is best-effort


def handle_ingest_state(request: Any) -> dict[str, Any]:
    body, metadata = parse_request(request)

    auth_err, resolved_owner = require_auth_owner(metadata)
    if auth_err:
        return auth_err

    content = (body.get("content") or "").strip()
    domain = (body.get("domain") or "").strip()
    environment = (body.get("environment") or "").strip()

    if not content:
        return response_payload(400, {"error": "bad_request", "message": "content is required"})
    if not domain:
        return response_payload(400, {"error": "bad_request", "message": "domain is required"})
    if not environment:
        return response_payload(400, {"error": "bad_request", "message": "environment is required"})
    if domain not in VALID_DOMAINS:
        return response_payload(400, {
            "error": "bad_request",
            "message": f"domain must be one of {sorted(VALID_DOMAINS)}",
        })
    if environment not in VALID_ENVIRONMENTS:
        return response_payload(400, {
            "error": "bad_request",
            "message": f"environment must be one of {sorted(VALID_ENVIRONMENTS)}",
        })

    system = body.get("system") or None
    tags = body.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    status = (body.get("status") or "current").strip()
    if status not in VALID_STATUSES:
        status = "current"
    supersedes_id = body.get("supersedes_id") or None
    valid_from = (body.get("valid_from") or "").strip() or None
    source = (body.get("source") or "api/ingest_state").strip()
    owner = resolved_owner or (body.get("owner") or "").strip() or "mmcmahon"
    auto_supersede = body.get("auto_supersede", True) is not False

    # Route through write_knowledge (ADR-018) so this endpoint gets the SAME guarantees as
    # /api/ingest: component_key derived from a component:* tag (P2), event-first auto-supersession
    # (P3 — sole writer of the superseded transition), an explicit valid_from (P5), and the
    # one_current_per_component invariant. It previously did a bare INSERT that silently dropped
    # all three (component_key=NULL, no event, valid_from forced to now()). write_knowledge owns
    # the embedding + the atomic event/insert.
    from api.knowledge_ingest import write_knowledge

    result = write_knowledge(
        content, owner, domain=domain, environment=environment, system=system,
        tags=tags, status=status, source=source, supersedes_id=supersedes_id,
        valid_from=valid_from, auto_supersede=auto_supersede,
    )
    if result.get("status") != "accepted":
        return response_payload(result.get("code", 500), {
            "error": result.get("error", "error"),
            "message": result.get("message", "ingest failed"),
            "details": result.get("details"),
        })

    try:
        with get_db_conn() as conn:
            _mark_wiki_pages_stale(conn, domain, system)
    except Exception:
        pass  # wiki staleness is best-effort

    return response_payload(200, {
        "status": "accepted",
        "id": result.get("id"),
        "owner": owner,
        "domain": domain,
        "environment": environment,
        "superseded_id": result.get("superseded_id"),
    })


def handle_propose_supersession(request: Any) -> dict[str, Any]:
    body, metadata = parse_request(request)

    auth_err, resolved_owner = require_auth_owner(metadata)
    if auth_err:
        return auth_err

    supersedes_id = (body.get("supersedes_id") or "").strip()
    content = (body.get("content") or "").strip()
    domain = (body.get("domain") or "").strip()
    environment = (body.get("environment") or "").strip()

    if not supersedes_id:
        return response_payload(400, {
            "error": "bad_request", "message": "supersedes_id is required",
        })
    if not content:
        return response_payload(400, {"error": "bad_request", "message": "content is required"})
    if not domain:
        return response_payload(400, {"error": "bad_request", "message": "domain is required"})
    if not environment:
        return response_payload(400, {"error": "bad_request", "message": "environment is required"})

    system = body.get("system") or None
    tags = body.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    source = (body.get("source") or "api/propose_supersession").strip()
    owner = resolved_owner or (body.get("owner") or "").strip() or "mmcmahon"
    valid_from = (body.get("valid_from") or "").strip() or None

    try:
        with get_db_conn() as conn:
            target = conn.execute(
                "SELECT id, status FROM public.knowledge WHERE id = %s",
                [supersedes_id],
            ).fetchone()
        if not target:
            return response_payload(404, {
                "error": "not_found",
                "message": f"Record {supersedes_id} not found",
            })
        if target["status"] != "current":
            return response_payload(409, {
                "error": "conflict",
                "message": (
                    f"Target record has status='{target['status']}' — "
                    "can only supersede 'current' records"
                ),
            })

        # Create the draft via write_knowledge so it carries component_key (P2) + valid_from (P5)
        # like any other write — a bare INSERT here dropped both, so a confirmed living doc landed
        # component_key=NULL. status='draft' + auto_supersede=False keeps it inert until confirm
        # promotes it and retires the target via an event (ADR-018 P3); supersedes_id records the
        # intended predecessor.
        from api.knowledge_ingest import write_knowledge

        result = write_knowledge(
            content, owner, domain=domain, environment=environment, system=system,
            tags=tags, status="draft", source=source, supersedes_id=supersedes_id,
            valid_from=valid_from, auto_supersede=False,
        )
        if result.get("status") != "accepted":
            return response_payload(result.get("code", 500), {
                "error": result.get("error", "error"),
                "message": result.get("message", "draft creation failed"),
                "details": result.get("details"),
            })
        proposal_id = result.get("id")
    except Exception as exc:
        return response_payload(500, {"error": "db_error", "message": str(exc)})

    return response_payload(200, {
        "status": "proposed",
        "proposal_id": proposal_id,
        "supersedes_id": supersedes_id,
        "owner": owner,
        "message": "Draft created. Call POST /api/confirm_supersession with proposal_id to commit.",
    })


def handle_confirm_supersession(request: Any) -> dict[str, Any]:
    body, metadata = parse_request(request)

    auth_err, _resolved_owner = require_auth_owner(metadata)
    if auth_err:
        return auth_err

    proposal_id = (body.get("proposal_id") or "").strip()
    if not proposal_id:
        return response_payload(400, {"error": "bad_request", "message": "proposal_id is required"})

    # Optional backdated fact-offset (ADR-018 P5): when the superseded fact stopped being true
    # earlier than we are recording it (a lab change we are documenting after the fact). NULL =>
    # the projection uses occurred_at (system-time), the P3 behaviour.
    fact_valid_until = (body.get("valid_until") or "").strip() or None

    try:
        with get_db_conn() as conn:
            draft = conn.execute(
                "SELECT id, status, supersedes_id, domain, system"
                " FROM public.knowledge WHERE id = %s",
                [proposal_id],
            ).fetchone()
            if not draft:
                return response_payload(404, {
                    "error": "not_found",
                    "message": f"Proposal {proposal_id} not found",
                })
            if draft["status"] != "draft":
                return response_payload(409, {
                    "error": "conflict",
                    "message": f"Proposal has status='{draft['status']}' — expected 'draft'",
                })

            superseded_id = str(draft["supersedes_id"]) if draft["supersedes_id"] else None
            if not superseded_id:
                return response_payload(409, {
                    "error": "conflict",
                    "message": "Proposal has no supersedes_id — cannot confirm",
                })

            target = conn.execute(
                "SELECT id, status FROM public.knowledge WHERE id = %s",
                [superseded_id],
            ).fetchone()
            if not target:
                return response_payload(404, {
                    "error": "not_found",
                    "message": f"Target record {superseded_id} not found",
                })
            if target["status"] != "current":
                return response_payload(409, {
                    "error": "conflict",
                    "message": (
                        f"Target record has status='{target['status']}' — "
                        "has it already been superseded?"
                    ),
                })

            now = datetime.now(timezone.utc)
            # Retire the target via an append-only event (ADR-018 P3); its projection trigger
            # performs the status='superseded' write — the sole writer of that transition. This
            # must precede promoting the draft to 'current' so one_current_per_component never
            # sees two current rows for the same component. The successor (proposal) already
            # exists, so the deferred superseding_id FK is satisfied immediately.
            conn.execute(
                """
                INSERT INTO public.supersession_events
                    (superseded_id, superseding_id, occurred_at, fact_valid_until,
                     reason_code, actor, method)
                VALUES (%s, %s, %s, %s, 'manual', %s, 'human')
                """,
                [superseded_id, proposal_id, now, fact_valid_until, _resolved_owner or None],
            )
            # Promote the successor from the same moment the prior fact ended, so the two
            # lifespans are contiguous (backdated offset when given, else now).
            conn.execute(
                "UPDATE public.knowledge SET status = 'current', "
                "valid_from = COALESCE(%s, %s) WHERE id = %s",
                [fact_valid_until, now, proposal_id],
            )

            domain = str(draft["domain"]) if draft.get("domain") else ""
            system = str(draft["system"]) if draft.get("system") else None
            _mark_wiki_pages_stale(conn, domain, system)
    except Exception as exc:
        return response_payload(500, {"error": "db_error", "message": str(exc)})

    return response_payload(200, {
        "status": "confirmed",
        "id": proposal_id,
        "superseded_id": superseded_id,
    })


def handle_query_state(request: Any) -> dict[str, Any]:
    body, metadata = parse_request(request)

    auth_err, resolved_owner = require_auth_owner(metadata)
    if auth_err:
        return auth_err

    query_params = metadata.get("query", {}) or {}

    domain = (body.get("domain") or query_params.get("domain") or "").strip() or None
    environment = (body.get("environment") or query_params.get("environment") or "").strip() or None
    system = (body.get("system") or query_params.get("system") or "").strip() or None
    status = (body.get("status") or query_params.get("status") or "current").strip()
    owner_param = (body.get("owner") or query_params.get("owner") or "").strip()
    owner = resolved_owner or owner_param or None

    try:
        limit = int(body.get("limit") or query_params.get("limit") or 10)
        limit = max(1, min(limit, 100))
    except (ValueError, TypeError):
        limit = 10

    where_clauses: list[str] = []
    params: list[Any] = []

    if status:
        where_clauses.append("status = %s")
        params.append(status)
    if domain:
        where_clauses.append("domain = %s")
        params.append(domain)
    if environment:
        where_clauses.append("environment = %s")
        params.append(environment)
    if system:
        where_clauses.append("system = %s")
        params.append(system)
    if owner:
        where_clauses.append("created_by = %s")
        params.append(owner)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)

    try:
        with get_db_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT id, content, domain, environment, system, tags,
                       status, valid_from, valid_until, supersedes_id,
                       source, created_by, created_at, ingest_id
                FROM public.knowledge
                {where_sql}
                ORDER BY valid_from DESC
                LIMIT %s
                """,
                params,
            ).fetchall()
    except Exception as exc:
        return response_payload(500, {"error": "db_error", "message": str(exc)})

    results = []
    for row in rows:
        r = dict(row)
        r["id"] = str(r["id"])
        if r.get("supersedes_id"):
            r["supersedes_id"] = str(r["supersedes_id"])
        for ts_field in ("valid_from", "valid_until", "created_at"):
            val = r.get(ts_field)
            if val is not None and hasattr(val, "isoformat"):
                r[ts_field] = val.isoformat()
        results.append(r)

    return response_payload(200, {
        "results": results,
        "count": len(results),
        "owner": owner,
    })
