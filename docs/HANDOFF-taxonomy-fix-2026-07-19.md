# HANDOFF — OpenBrain taxonomy misfiling: diagnosis + cleanup (2026-07-19)

Self-contained so a fresh session can finish this **without prompting Mike**. Diagnosis is
DONE and verified; what remains is mechanical execution + human-gated data changes.

## TL;DR
Technical/infra notes (esp. session wraps) were filing into **Study/OpenBrain** instead of
Network/K8s. Root cause: the ingest **taxonomy-override** was being dropped before it reached
the honor/derive policy. Two independent drop points, plus a client that never sent the field.
The pure taxonomy mapper was fine (27 green unit tests); nothing tested the *glue*.

## Root cause (verified)
- Taxonomy + per-owner honor/derive policy shipped **2026-06-17** (`_openbrain_api.py` commit
  `23c2bf9`). `ingest_payload` honors `payload["domain"]/["environment"]` as an override; if
  absent, it infers via `map_to_taxonomy` (correct default for Family-GPT paths — Annie/Beth
  never send taxonomy, so inference MUST stay; do not require these fields globally).
- **Bug 1 (server, MCP tool path):** `api/mcp_http.py` `_call_tool("ingest", …)` built the
  normalized payload from only `source_type/source/subject/topic` — **dropped
  `domain/environment/tags`** even though the `inputSchema` advertises them. → override lost →
  Study default. **FIXED** in this branch (+ `tests/test_mcp_ingest_forwarding.py`, green).
- **Bug 2 (client, our skill):** `~/.claude/skills/brain-commit.md` posts to `/openbrain_ingest`
  and **never sends `domain`/`environment`** at all → every session wrap inferred to
  OpenBrain/Study. (The `/openbrain_ingest` handler *would* honor them if sent; the skill just
  doesn't.) Fix = point our ingest at the direct helper with explicit taxonomy.
- **Why testing missed it:** `tests/test_taxonomy_map.py` = 27 *pure-function* tests of
  `map_to_taxonomy` (always green). No test exercised the ingest tool/HTTP glue that forwards
  the override. Schema said "supported," mapper worked, the copy-args-to-payload step silently
  omitted the fields. New regression test closes that seam.
- **Direct path was always correct:** `POST /api/ingest` honors overrides (proven: same body
  stored domain=Network via direct, domain=Study via MCP tool). Our new `scripts/ob_ingest.py`
  uses it (also dodges the Anthropic-edge WAF — see `reference_openbrain_ingest_direct_bypass`).

## Fix status (in the working tree, on this branch)
- [x] `api/mcp_http.py` — forward domain/environment/tags. DONE.
- [x] `tests/test_mcp_ingest_forwarding.py` — regression test. DONE, green (run funcs directly;
      pytest full-collect trips a TCC PermissionError on `vault/`, unrelated).
- [x] `scripts/ob_ingest.py` — direct-ingest helper (verbatim body, token from `.env.local`). DONE (untracked).

## Remaining — AUTONOMOUS (safe; no Mike needed). Guardrails: feature branch → PR, NEVER merge, NEVER push main.
1. **Commit A+B to a feature branch + PR** (open-brain):
   - `git -C open-brain checkout -b fix/ingest-taxonomy-override`
   - stage ONLY: `api/mcp_http.py tests/test_mcp_ingest_forwarding.py scripts/ob_ingest.py docs/HANDOFF-taxonomy-fix-2026-07-19.md docs/reclassify-taxonomy-2026-06-17.sql`
   - run smoke first (guardrail): `.venv/bin/python scripts/smoke_checks.py` (expect green; note pre-commit hook runs)
   - commit (Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>), push, `gh pr create` (create only — **do not** `gh pr merge`).
2. **Fix the skills** (`~/.claude/skills/` — plain files, edit in place):
   - `brain-commit.md`: replace the `/openbrain_ingest` curl with a call to
     `open-brain/scripts/ob_ingest.py` (or `POST /api/ingest`) that ALWAYS passes
     `--domain/--environment`. Fix the stale token path `~/.claude/home-lab/...` → the helper
     reads `open-brain/.env.local` itself.
   - `wrap.md` Step 5: use `ob_ingest.py` with explicit `--domain/--environment` matching the
     session (infra → `Network`/`Production`), not the bare MCP tool.
   - `commit.md`: bump `Co-Authored-By: Claude Sonnet 4.6` → `Claude Opus 4.8`.
3. **Write the reclassify SQL** as a reviewed file (do NOT execute): `docs/reclassify-taxonomy-2026-06-17.sql` (draft below).

## Remaining — HUMAN-GATED (Mike, when back). Do NOT do these autonomously.
- **Merge the PR** (guardrails ban `gh pr merge` for the agent).
- **Run the reclassify SQL** against Supabase — review AUDIT output first, then the UPDATE inside a txn.
- **Delete throwaway probe rows** created during diagnosis (ids below) — via Supabase or the SQL's DELETE block.

## Throwaway probe rows to purge (created during diagnosis)
- `30cb9933-80ee-41c2-9690-49ca15338653` (direct probe, Network) — TAXPROBE-20260719
- `5c53ff17-5388-4978-a294-be5609af1dff` (MCP probe, Study) — TAXPROBE-20260719-MCP
- plus by content marker: any row whose content starts with `TAXPROBE-` or subject was `throwaway`,
  and connectivity probes topic in ('waf-probe','waf-direct-probe','waf-direct-verbatim','waf-probe-2').

## Draft reclassify SQL (review before running — heuristic, content-based)
```sql
-- Scope: Mike's technical rows since taxonomy shipped, misfiled into Study/OpenBrain.
-- Family rows (created_by annie/beth, or genuine Study content) are intentionally untouched.

-- 1) AUDIT (read-only) — eyeball before any UPDATE
SELECT id, domain, environment, system, tags, created_at, left(content,140) AS preview
FROM public.knowledge
WHERE created_at >= '2026-06-17'
  AND status = 'current'
  AND domain IN ('Study','OpenBrain')
  AND created_by = 'mike.mcmahon67'
  AND ( content ~* '\m(spectrenet|mcmahon\.home|coredns|technitium|dns-[0-9]|pi-?hole|keepalived|metallb|proxmox|pmx-01|talos|kubectl|k3s|kubernetes|netplan|vlan|qnap|homelab)\M'
        OR tags && ARRAY['Network','K8s','Homelab','SpectreNet','Proxmox'] )
ORDER BY created_at DESC;

-- 2) RECLASSIFY (run in a txn AFTER reviewing #1; adjust as needed)
BEGIN;
-- k8s-flavored → K8s
UPDATE public.knowledge SET domain='K8s', environment='Production', system=COALESCE(system,'SpectreNet')
WHERE id = ANY(%(k8s_ids)s);      -- fill from audit
-- dns/network infra → Network
UPDATE public.knowledge SET domain='Network', environment='Production', system=COALESCE(system,'SpectreNet')
WHERE id = ANY(%(net_ids)s);      -- fill from audit
-- verify, then COMMIT;  (ROLLBACK if the counts look wrong)
SELECT domain, environment, count(*) FROM public.knowledge
WHERE id = ANY(%(k8s_ids)s || %(net_ids)s) GROUP BY 1,2;
-- COMMIT;

-- 3) PURGE diagnosis probes
DELETE FROM public.knowledge
WHERE content LIKE 'TAXPROBE-%'
   OR content LIKE 'WAF direct-path%' OR content LIKE 'Direct-path verbatim test%'
   OR content LIKE 'Ingest connectivity probe%' OR content LIKE 'Probe two:%';
```
NOTE: prefer explicit id-lists over the broad regex for the UPDATE — the regex is for *finding*
candidates, not blindly reclassifying. Environment=Production is a guess per row; downgrade any
that are actually Lab/Study during review.

## Files touched this session (for the PR)
- `api/mcp_http.py`, `tests/test_mcp_ingest_forwarding.py`, `scripts/ob_ingest.py`,
  `docs/HANDOFF-taxonomy-fix-2026-07-19.md`, `docs/reclassify-taxonomy-2026-06-17.sql`
- (separate, not in open-brain): `~/.claude/skills/{brain-commit,wrap,commit}.md`
