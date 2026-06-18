# The Flip: Cutting Over a RAG System I Forgot to Finish

> A migration war story about a personal knowledge base, a bug hiding in plain
> sight, a deploy pipeline that had been quietly dead for weeks, and a one-line
> ignore file that took down every route in production. Also: why engineers need
> PM support — even when the engineer and the customer are the same person.

---

## The system

OpenBrain is my family's RAG knowledge base. Supabase (pgvector) is canonical
storage, a handful of serverless functions on Vercel are the API, and three Custom
GPTs sit on top — one for me, one for my wife, one for my 13-year-old daughter, who
uses hers as a study tutor. It's the kind of project you build to scratch your own
itch and then quietly depend on.

Like a lot of self-serving projects, it had accumulated a second, better version of
itself that never shipped. Call it OB2: a proper `knowledge` table with a temporal
lifecycle (`current` vs `historical`), a controlled taxonomy, a wiki-compilation
layer. All designed. All built. All sitting there doing nothing while production
quietly ran on the old path.

I didn't set out to fix that. I set out to load some flashcards.

## How it started: 31 Linux flashcards

The task was small. My daughter is learning the Linux command line over the summer,
so I'd generated 31 reference cards — `ls`, `cd`, `grep`, `chmod`, the usual Phase-1
through Phase-3 progression — and wanted them in her vault so her tutor GPT could
quiz her. Ingest them, validate retrieval, done. A twenty-minute job.

Ingest succeeded: 31 of 31. Then I validated retrieval, and every single query came
back with **exactly one result.** Not one *good* result — one result, total,
regardless of the question, served with suspiciously high confidence.

## The bug hiding in plain sight

The root cause was a fusion-key bug in the retrieval layer. The system uses
Reciprocal Rank Fusion to blend keyword and vector search, and the fusion was keyed
on `(file, source)`. For chat-ingested text, both of those are null — so every text
row in the vault collapsed into a single bucket `(None, 'text')`, and the fusion
surfaced *one* row as the answer to everything.

This had been live for weeks. It passed the smoke tests (more on that later). It
"worked" in the sense that queries returned `HTTP 200` and a plausible-looking chunk.
Nobody noticed because nobody had sat down and asked the boring question: *for ten
different queries, do I get ten meaningfully different answers?* That's not a
debugging question. That's a QA question. There's a theme forming.

## The bigger discovery

Chasing the fusion bug, I went to look at whether OB2 — the better version — would
have the same problem. That's when I found the real situation:

- `public.knowledge` had 699 rows, **all `status='historical'`**, newest timestamp
  five weeks old. A frozen migration snapshot. Zero `current` rows.
- There was **no semantic retrieval path** that read `knowledge` at all.
- The family ingest path wrote only to the old `thoughts` table.
- The wiki layer had compiled exactly zero pages.

OB2 wasn't broken. It was *stalled* — finished in every sense except the one that
matters. Someone (me) had built the whole thing and then never crossed the last
mile: the cutover. The code reviews passed, the migrations ran, the ADRs got
written, and then the ball just... stopped. No one owned "is this actually live?"
because on a solo project there's no one whose job that is.

So the twenty-minute flashcard task became: finish the cutover I'd abandoned.

## The decision: do it properly, behind gates

The tempting move was to hot-patch the fusion bug in the old path and walk away. But
the old path was a dead end I'd already replaced — patching it was polishing a thing
I wanted to delete. The right move was to finish OB2 and flip to it.

The constraint I set: **every step reversible, nothing flipped until everything was
verified.** A personal project doesn't get a maintenance window or a rollback runbook
unless you write one. So I built one.

What landed:

- **Fixed retrieval** (`knowledge_retrieval.py`) — RRF keyed on the unique
  `knowledge.id` (a UUID), so distinct rows never collapse. The original bug, designed
  out.
- **Integrity-checked ingest** (`knowledge_ingest.py`) — rejects empty/invalid
  taxonomy instead of silently substituting junk; deterministic idempotency key.
- **Taxonomy governance** — a controlled tag vocabulary with the DB table as the
  runtime source of truth, a code seed as fallback, and a proposal queue so unknown
  tags get reviewed instead of dropped or sprawling. Two schemes had already drifted
  apart; this stops the drift.
- **Phase-2 wiring behind env flags** — `OPENBRAIN_READ_TARGET` and
  `OPENBRAIN_WRITE_TARGET`, both defaulting to `thoughts`. The new code shipped
  **dormant**: every read and write still went to the old path until I explicitly
  flipped a flag. That made the whole thing A/B-testable and the flip itself a
  one-variable change.

Two design calls worth calling out, because both came from thinking about the actual
humans using this:

**Temporal awareness is a priority, not a filter.** My first cut had the read path
serve only `status='current'`. That's correct for "what's the live state of the
homelab?" and catastrophically wrong for a study tutor — it would have hidden ~95% of
my daughter's and wife's notes, all of which were `historical` from the migration
snapshot. The fix: prefer `current`, but broaden to `historical` when the top result
is low-confidence or empty. Stale operational state stays out of the way; nobody's
notes disappear.

**Per-owner ingest policy.** When you ingest a note, the system can either *honor* an
explicit domain/environment you provide or *derive* it from the content. I'm more
likely to classify correctly than my 13-year-old is, so: my account honors explicit
values (and warns me if they disagree with what it inferred — typo protection),
while the family accounts auto-derive. Same code, different trust levels.

## The flip

The cutover ran as a gated sequence, each step reversible:

1. Back up both tables.
2. `retag` the existing rows to the canonical vocabulary (37 updates, 19 smoke-test
   rows deleted).
3. Apply the vocabulary + governance migrations.
4. `cutover_migrate --execute` — 63 gap rows into `knowledge`, each tagged
   `source='cutover:<id>'` so the entire migration reverses with one `DELETE`.
5. `promote_study_current --execute` — promote the 638 non-superseded study/personal
   rows from `historical` to `current` (operational rows stay historical; their live
   version is governed by supersession, not a blanket promotion).
6. Flip the two Vercel flags to `knowledge`.
7. Validate.

The old `thoughts` table was never touched — it stayed as a hot rollback standby.
Worst case at any point: flip the flags back, redeploy, and you're on the old path in
under a minute.

And it worked. Production started serving `knowledge`, my daughter's 31 cards came
back `current` under *her* account with the right tags, owner isolation held. Clean.

Then I tried to deploy the code, and the actual adventure began.

## The deploy saga (or: the parts a PM would have caught)

Here's where the "even self-serving projects need process" thesis stops being cute
and starts being the whole point.

**The pipeline had been dead for weeks.** I went to verify the flip was live and the
production API was serving the *old* shape — no new code at all. It turned out Vercel
hadn't deployed a single commit since a PR five merges back. The GitHub→Vercel
integration had silently lost access to the repo. Every merge since had been a no-op
deploy. Nobody noticed because nobody was watching deploy health — there's no
dashboard alert on "your CI has quietly stopped." A PM would have a status check.
I had vibes.

**The CLI deploy hit a wall.** Fine, I'll deploy manually. `vercel --prod` promptly
died with `EACCES` on a cache file — the CLI bundles the *entire working directory*,
including local caches owned by a different user, secrets in `.env.local`, and a
multi-megabyte vector index. None of that belongs in a serverless bundle. So I added
a `.vercelignore`.

**And then I took down every route in production.** The new deploy returned `HTTP
500` on *everything* — even `/health`, which does nothing. The function was crashing
at import: `ModuleNotFoundError: No module named 'api.app'`. The router was missing
from the bundle.

The cause, which I will be thinking about for a while: my `.vercelignore` had a line
that said `app.py` — meant to exclude a root-level dev script. But `.vercelignore`
uses gitignore semantics, where a bare filename matches **at any depth.** So `app.py`
also excluded `api/app.py` — the entire router. One unanchored pattern, every route
down. The fix was a single character: `/app.py`. The lesson was bigger than the fix.

**The verified-commits red herring.** Once auto-deploy *should* have been working, it
still wasn't firing on merge. I went down a rabbit hole on a "require verified
commits" setting before checking the obvious thing: the GitHub App had been
re-granted access to the org but not to *this repository*. Select-repositories
access, openbrain not selected. Grant it, reconnect, and a normal PR merge finally
produced a normal production deploy.

Every one of those failures was a process gap, not an engineering gap. The code was
fine. What was missing was someone asking: *Is it deployed? Are you sure? Did the
merge actually trigger a build? Is the thing you're looking at the thing you shipped?*

## Hardening the part that lied to me

Remember the fusion bug that passed the smoke tests? The smoke suite checked that
`/query` returned `HTTP 200`. It did not check that the response contained anything.
A broken database returns `200` with `results: []`, which is indistinguishable from
"healthy but no match." That blind spot is *how the original bug lived for weeks.*

So the last thing I did was add a content-level check: query as a real owner, assert
real rows come back with non-empty text, and report which backend served them (so a
silent flip-revert shows up too). A healthy backend always returns rows; empty means
genuinely broken. The smoke test can no longer tell me everything's fine while the
database is on fire.

## Why engineers need PM support — even self-serving ones

Here's the thing that ties the flashcards, the stalled cutover, the dead pipeline,
and the route-killing ignore file together: **none of them were hard engineering
problems. They were all "cross the i's and dot the t's" problems.** (Yes, I know it's
the other way around. That's the joke — even the idiom about getting the details
right gets the details wrong.)

The engineering — the retrieval fusion, the temporal model, the gated migration, the
taxonomy governance — that part was the fun 80%. The 20% that bit me every single
time was the unglamorous follow-through:

- *Did we actually ship the thing we built?* (No — OB2 sat stalled for over a month.)
- *Is the pipeline that ships it even alive?* (No — dead for weeks, silently.)
- *Does the test verify the behavior, or just the status code?* (Just the status
  code — for weeks.)
- *Did that config change do exactly what you think, and only that?* (No — it ate the
  router.)

A PM, or really anyone playing the "are we done-done?" role, exists to force those
questions before they become incidents. On a team, that role is a person. On a solo
project, it's a discipline you have to manufacture, because you are simultaneously the
engineer who wants to move on and the only person who'll notice if you didn't finish.

When you're your own customer, you give yourself the worst possible PM: one who trusts
the engineer completely. I built a perfectly good system and then, as its own product
manager, never asked whether it had launched. The cutover wasn't a coding project. It
was the project-management project I'd been skipping.

## Where it landed

OpenBrain now runs on `knowledge`: governed taxonomy, temporal-aware retrieval,
per-owner ingest policy, the fusion bug designed out, the deploy pipeline restored,
and a smoke test that can't quietly lie about content anymore. My daughter's flashcards
work. The twenty-minute task took considerably longer than twenty minutes.

Worth it. And next time, before I call something "built," I'm going to make myself ask
the boring question first.
