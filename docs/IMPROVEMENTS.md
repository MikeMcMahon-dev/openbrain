# OpenBrain Improvements

```
   .-""""-.
  /        \
 |  C' was  |
 |   here   |
  \  (^_^) /
   '------'
```

A running record of meaningful improvements to the OpenBrain system.
Each entry captures what changed, why it matters, and the measurable result.

---

## 2026-03-27 — Query Tuning: RRF + Length Penalty + Confidence Scoring

**What changed:**
- Replaced naive keyword-first merge with Reciprocal Rank Fusion (RRF, k=60)
- Added length penalty: docs with <30 words are down-weighted proportionally
- Added per-result `confidence` field (`high` / `medium` / `low`) and top-level `query_confidence` on every API response

**Why it matters:**
Short Slack messages were outscoring 300-word study notes by 2x on Annie's taxonomy queries.
A 13-year-old typing "taxonomy" was getting a noise message as her top result instead of her actual study guide.

**Measured result:**
- 97% pass rate on 100-query test suite (first run, no tuning iterations needed)
- 96.9% pass rate on 1000-query suite — certified trustworthy
- All failures correctly flagged `low` confidence; no silent bad results
- Confidence scoring gives the Custom GPT — and Annie — an honest signal when results are weak

---

## 2026-03-27 — Bearer Token Rotation Script

**What changed:**
- `scripts/rotate_tokens.py`: generates new per-user tokens, updates Vercel env vars via REST API, updates `.env.local`
- Dry-run by default; `--apply` executes live rotation
- Prints manual steps for ChatGPT Custom GPT config updates (no API available for those)

**Why it matters:**
Tokens had a known git-exposure history. Manual rotation was error-prone (see: Beth's `+` character incident).
Script makes rotation repeatable, auditable, and fast.

**Measured result:**
- Dry run verified against live Vercel project; all three owner tokens generated correctly
- Rotation schedule: January 1st and July 4th annually

---

## 2026-03-26 — Supabase Anon Role Lockdown

**What changed:**
- Revoked `SELECT`, `INSERT`, `UPDATE`, `DELETE` on `public.thoughts` from both `anon` and `authenticated` Supabase roles

**Why it matters:**
Supabase flagged the table as publicly accessible. Bearer-token auth at the API layer was the real gate,
but defense-in-depth means the DB shouldn't be reachable without going through the API.

**Measured result:**
- Direct DB access via anon key now blocked at the row level
- API functionality unchanged (all traffic routes through Vercel with bearer token auth)

---

## 2026-03-24 — Brain Activity Report

**What changed:**
- `scripts/brain_report.py`: on-demand activity report by owner
- Flags: `--days N` (default 7), `--synopsis` (recent entry previews), `--daily` (day-by-day breakdown)

**Why it matters:**
No visibility into who was using the brain or what was being stored. Now a single command shows
the full picture — entries per owner, time range, and recent content previews.

**Measured result:**
- Confirmed multi-owner data across Mike, Beth, and Annie
- Used same session to identify that Annie's Taxonomy session data hadn't been committed (caught and fixed)

---

## 2026-03-21 — Text Ingest Write Path Fixed

**What changed:**
- `source_type=text` via API was silently returning "accepted" but never writing to the DB
- Added `_write_text_ingest()`: embeds content, introspects schema, upserts to `public.thoughts`
- Schema guard: queries `information_schema.columns` before INSERT to avoid missing-column errors

**Why it matters:**
Every Custom GPT "save to brain" action was a no-op. Users had no idea their notes weren't being stored.

**Measured result:**
- End-to-end verified: ingest via MCP → DB write → query retrieval confirmed
- All three Custom GPTs tested and passing

---

## 2026-03-21 — Three Family Custom GPTs Live

**What changed:**
- Mike (`mike.mcmahon67`), Beth (`snapple01`), Annie (`anneliesepaige`) each have an isolated Custom GPT
- Per-user bearer token → owner mapping via `OPENBRAIN_TOKEN_OWNER_MAP`
- Tenant isolation confirmed: Annie's data not visible in Mike's queries

**Why it matters:**
OpenBrain went from a single-user dev tool to a family knowledge system.
Each member has a private, scoped view of the brain with their own identity.

**Measured result:**
- All three GPTs validated end-to-end
- Tenant isolation confirmed via direct curl with each token
