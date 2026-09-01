# Retirement runbook — removing something from the vault

The removal airlock (migration 012). Agents can **request** a removal; only you can **perform**
one. This is deliberately a two-person protocol with one person in it.

You will not do this often, which is exactly why it is written down. Worked example at the
bottom is a real run from 2026-08-25, output included.

---

## The one decision that matters: retire or delete

| | `retire` (default) | `delete` |
|---|---|---|
| what happens | appends an expiry row to `supersession_events` (`superseding_id` NULL); the projection sets `status='historical'` | hard `DELETE` of the row and its chunks |
| content | **preserved**, still reachable via an as-of read | **gone**, irreversible |
| when it is legal | always | only when nothing references the row |
| reversible | effectively yes — the row is still there | no |

**The trap: a `retire` FORECLOSES a later `delete`.** Once an immutable supersession event
references the row, the FK pins it permanently — and that FK is not deferrable. Retiring is both
the safe default *and* a one-way door away from deletion. If you genuinely mean delete, say so the
first time.

Rule of thumb:

- **Retire** anything that was ever true — superseded state, a duplicate that competed for
  `current`, a decommissioned system's notes. History has value; retrieval-noise is the problem,
  and `historical` solves it.
- **Delete** only test scaffolding and probe artifacts — canaries, smoke residue, an ingest that
  was a mistake in the moment and carries no knowledge. Retiring these leaves the vault carrying a
  permanent historical record of your own debugging litter.

---

## The flow

Four steps, and approving is not executing. They are separate commands on purpose so a mis-click
cannot remove anything.

```
agent proposes  ->  you list/show  ->  you approve  ->  you execute
   (queued)           (review)         (decision)      (it happens)
```

### 1. An agent proposes (you rarely do this part)

Via the `propose_retirement` MCP tool, or `api/retirement_request.py`. It queues a request and
removes nothing. The response carries the evidence it collected — reference counts, age, whether
a hard delete is even legal.

An agent may only propose removal of rows it owns.

### 2. See what is pending

```bash
cd /Users/Shared/home-lab/open-brain
.venv/bin/python3 scripts/retirement_review.py list
```

Each entry shows the target, its method, who asked, the reference counts, and the rationale. Read
the rationale — **a request you cannot evaluate should be denied**, not approved to clear the
queue.

For the full target content plus evidence:

```bash
.venv/bin/python3 scripts/retirement_review.py show <request_id>
```

### 3. Decide

```bash
.venv/bin/python3 scripts/retirement_review.py approve <request_id> --note "why you agreed"
.venv/bin/python3 scripts/retirement_review.py deny    <request_id> --note "why you did not"
```

Nothing has been removed yet at this point. The note is the record of your reasoning.

### 4. Execute

```bash
.venv/bin/python3 scripts/retirement_review.py execute              # every approved request
.venv/bin/python3 scripts/retirement_review.py execute <request_id> # just one
```

It lists what it is about to do and waits for you to type `yes`. `--yes` skips the prompt; do not
use it interactively — the prompt is the last thing standing between a typo and a hard delete.

**Execution re-checks the evidence.** The reference counts captured at request time go stale: a
row that was safe to hard-delete an hour ago may since have been referenced by a supersession
event, and that FK is not deferrable. On any drift it refuses and marks the request `failed`
rather than pushing through. That re-check is the whole reason execution is a separate step from
approval.

---

## Verifying it actually happened

The script reports what it did. Check the vault anyway — the standing rule is that a success
message is a claim, not evidence.

```bash
.venv/bin/python3 - <<'PY'
import os, sys
sys.path.insert(0, '.')
for line in open('.env.local'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))
from api.knowledge_ingest import get_db_conn
TARGET = 'a9eb6341'          # <- the id prefix you acted on
with get_db_conn() as conn:
    r = conn.execute("SELECT id::text AS id, status, valid_until FROM public.knowledge "
                     "WHERE id::text LIKE %s", (TARGET + '%',)).fetchone()
    print(r or f"{TARGET}: GONE from knowledge")
    n = conn.execute("SELECT count(*) AS n FROM public.knowledge_chunked "
                     "WHERE document_id::text LIKE %s", (TARGET + '%',)).fetchone()['n']
    print("chunks remaining:", n)
PY
```

What you should see:

- **after a retire** — `status=historical`, `valid_until` stamped, **chunks still present**. The
  chunks staying is correct: the content is preserved and readable as-of.
- **after a delete** — the row is gone and `chunks remaining: 0`. A delete that left chunks behind
  would be an orphan, and `scripts/chunk_integrity_check.py` would flag it.

Reading a retired row back:

```bash
.venv/bin/python3 scripts/as_of.py 2026-08-24 --system SpectreNet
```

That shows everything whose real lifespan contained that date, including rows since retired.

---

## Worked example — 2026-08-25

Two duplicate Internal-CA notes were written two minutes apart, 85.5% identical. The keeper
(`fd69eb6c`) carried the `component:internal-ca-client-trust` identity tag; the other
(`a9eb6341`) had no component key, which is exactly why it appended as a second competing
`current` row instead of superseding the first. Plus one diagnostic canary of Claude's to remove.

```
$ .venv/bin/python3 scripts/retirement_review.py list
2 pending:

  f429a1fd-003d-488a-b10e-99a6cf90f0d3
    target : a9eb6341  [current]  'Internal CA — Windows client trust, findings 2026-08-24'
    method : retire  (component_collision)   by mike.mcmahon67   2026-08-25 04:13
    refs   : events=0 parents=0 contradictions=0 chunks=3 age=0d

  a21bc338-db83-4472-b370-a22ace290851
    target : 7bd90af1  [current]  'Canary 2026-08-25T01:56:56Z: short note, canonical system,'
    method : delete  (manual)   by mike.mcmahon67   2026-08-25 15:14
    refs   : events=0 parents=0 contradictions=0 chunks=1 age=1d
```

Before approving the retire, the duplicate was diffed against the keeper to confirm nothing
unique was lost — the keeper covered every section the loser had, plus two extra `certutil`
commands and a chain note. Different methods for a reason: the CA note was *true*, just
duplicated, so it retires; the canary was scaffolding, so it deletes.

```
$ ... approve f429a1fd-... --note "duplicate of fd69eb6c; keeper has the component key"
f429a1fd-003d-488a-b10e-99a6cf90f0d3 -> approved by mmcmahon
Nothing removed yet. Run `execute` to perform it.

$ ... execute
About to execute 2 approved request(s):
  retire  a9eb6341  (component_collision)
  delete  7bd90af1  (manual)

Proceed? type 'yes' to continue: yes
  OK      f429a1fd: retired (status -> historical)
  OK      a21bc338: deleted (1 row, 1 chunks)
```

Verified after the fact:

```
a9eb6341 retired dup     -> status=historical  valid_until=2026-08-25 15:19:06+00  chunks: 3
fd69eb6c KEEPER          -> status=current     valid_until=None                    chunks: 4
7bd90af1 deleted canary  -> GONE from knowledge                                    chunks: 0
supersession events for a9eb6341: [('component_collision', superseding_id=None, '08-25 15:19')]
```

`superseding_id=None` is what an expiry looks like — the row ended without a successor, as opposed
to a supersession where one note replaces another.

---

## Preventing the work in the first place

Nearly every retire so far has been the same cause: **a current-state note ingested without a
`component:` identity tag**, so it appended as a rival `current` row instead of replacing its
predecessor in place.

Ingest living docs with the identity and the problem does not arise:

```bash
.venv/bin/python3 scripts/ob_ingest.py --file note.md --subject spectrenet-dns \
    --domain Network --environment Production --system SpectreNet \
    --component dns-current-state
```

Session wraps, incident reports and other append-only history correctly have no `--component`.
See ADR-008.
