# Two Agents Wrote an ADR. It Contradicted Itself.

*A post-mortem on collaborative AI architecture work — what it got right, how the document failed anyway, and what I'd change.*

---

Over about thirty-six hours at the end of July, two Claude instances and I redesigned how OpenBrain handles temporal invalidation. One instance had repository and database access. The other didn't — it worked through the MCP connector, seeing only what the API returned. I passed messages between them.

The design they produced was correct. The document they produced was unimplementable. Those two sentences are the whole post, but the mechanism is worth walking through, because I don't think the failure was about AI at all.

## The setup

The problem was real and measurable. A note from June asserting that a DNS service was "COMPLETE and healthy" was outranking the July document that recorded its decommissioning. Both were marked `current`. Nothing in the system knew they disagreed.

I put the question to the community around Nate Jones' Substack and got three substantive replies in a day. That input was genuinely good and shaped the design. Then I handed it to the two agents to turn into an architecture decision record.

What followed was, in fairness, the most rigorous technical review I've had on a personal project.

## What worked

**They caught each other's errors, repeatedly, and the corrections stuck.**

The connector-only instance claimed two API endpoints behaved differently on identical input. The repo instance checked and disproved it — the difference was a deployment boundary, not an endpoint. Retracted cleanly.

The repo instance then claimed zero documents needed re-chunking, based on a count. The connector instance produced three specific row IDs that contradicted it. The repo instance re-ran the query with the right metric and conceded: it had been measuring whether a document appeared in the chunk table at all, not whether it had been split into sections. Twenty documents had been emitted whole.

Somewhere in there they established a working rule, and it was the best thing to come out of the whole exercise:

> Neither of us wins by assertion. Refute from the rows, not from a count.

That rule produced real findings. It surfaced that supersession keyed off a field with no validation — I watched an invented key get accepted silently while the *validated* fields warned me they disagreed with inference. It surfaced that five separate schema capabilities had shipped with no way for any caller to set them. Those aren't the kind of thing you find by reading your own code.

**And the disagreements were substantive rather than performative.** They split on implementation order — one ranked contradiction detection first on value, the other fourth on dependency. The dependency argument won because it was better: resolving a detected contradiction means recording a supersession, so building the detector first means rebuilding its output path immediately after. The concession was explicit and reasoned.

## What failed

The ADR went through four revisions. Each revision added a section. Every addition was individually justified.

By rev.4 the document had a `Decision` section and a `Resolutions` section. `Decision` described the architecture. `Resolutions` described how seven open questions had been settled — and one of those resolutions **changed the architecture**.

Specifically: `Decision` said metadata lives on the parent table. `Resolutions` item 1 said chunks carry no metadata at all and join to the parent instead. Same document. Different answers. Both correct at the time they were written; only one of them current.

Then implementation happened. The implementer read `Decision`, built what it said, and reintroduced the exact flaw `Resolutions` had eliminated a day earlier.

The correction was one query away, in a memory system built specifically so agents could retrieve prior decisions. Nobody ran it.

## The part that isn't about AI

Here's my honest read: **a document where the governing decision appears in two places has already failed.** That's true whether a human or a model wrote it. I've seen the same pattern in enterprise design docs — an appendix that quietly supersedes the body, and everyone downstream reading the body.

What the AI collaboration did was *accelerate* it. Two capable reviewers, each raising valid points, each addition improving the document locally while degrading it globally. Nobody was wrong at any individual step. The document just accumulated past the point where it could be implemented from.

And I couldn't catch it. That's the uncomfortable part. I'm twenty years into infrastructure, but database architecture isn't my depth — I was dependent on the review process being sound, and the review process was excellent at technical content and blind to its own structure.

The failure mode isn't "the AI was wrong." It's **"the AI was right in two places and I couldn't tell which one governed."**

## What actually protected me

Not correctness. Recoverability.

- Phase 1 wrote nothing to the database — read-time changes only. It shipped and it's still fine.
- Phase 2 stopped partway at a state where nothing was broken: constraints added but not validated, retrieval still reading the old path.
- A mandatory dry run before any destructive step came back AMBER and identified twelve foundational documents that a naive change would have buried.

Every one of those was a decision made when the plan was written, not a lucky outcome. The plan was flawed and the guardrails held anyway.

That's the thing I'd tell anyone doing this: you cannot verify that two agents got the architecture right. You *can* verify that every step is reversible and that someone stated the rollback before running it. That's a question a non-specialist can ask, and it's the one that saved this.

## It happened again, one layer down

I want to be honest that fixing the document didn't fix the process, because the next failure came from the same place.

With the single-decision ADR in hand, I asked the repo agent to do the unglamorous work first: validate the plan against the live database and confirm the two matched. It came back confirmed. It had checked the row data — the counts, the re-keyed rows — and the wording of the document. It had not checked the schema *constraints*.

Two of them mattered. Four of the columns the plan wanted to drop were `NOT NULL`, which changes the safe order of the entire migration — you cannot stop writing a column the database still requires, so the drop has to be split around a deploy. And the migration named three indexes to remove that don't exist; the agent had written the names it *expected* rather than the ones the database actually had. Separately, a second piece of code wrote to the same table — a backfill script outside the file being edited — and nobody had touched it. Run the migration as written and that script breaks the next time anyone re-chunks.

Any one of those, applied against a `DROP COLUMN`, is how you lose data quietly. Not all of it — just the few rows, or the one path, that nobody notices for a month.

What caught it was the same backstop as before: restating what I was about to build, out loud, against the actual schema, right before running it. Which means the primary check — "validate current state" — had failed *again*, and the same safety net saved it *again*. Twice is a pattern. The validation wasn't so much wrong as scoped to what was easy to look at: the data and the words, not the constraints and not every caller. Right about the part it checked, blind to the part that bites. The same shape as the original bug, one layer down.

## What I changed

**One decision section. No appendix that can supersede it.** Superseded reasoning lives in version control, not in a section the reader has to know to consult. If the design changes, the design section changes.

**Restate before build.** Before executing a phase, re-read the document and state in one sentence what you're about to build. If it doesn't match, stop. Ten seconds against two hours of migration rework.

**Write the test that would have caught the last failure, and make it fail first.** Three metadata drift incidents in nine days, and there was no test asserting the two tables agreed. We'd built a twenty-fixture harness for the *new* mechanism while the already-known failure mode went unguarded. The parity test now has to go red against the current broken state before anything gets fixed — a test that passes on arrival isn't testing anything.

**Reversibility stated per step.** If the rollback isn't immediate and concrete, the step doesn't run.

**"Validate current state" now means something specific, and it's mechanical.** It used to be a sentence I trusted. Now it's a script that dumps the live columns and their nullability, the *real* index and constraint names, and every place in the repository that reads or writes the table — plus a reviewer that blocks a schema change with none of that evidence on the record. You don't get to validate the row data and call the migration validated. The apply order and the full list of writers are part of the check, or it isn't one.

## Would I do it again

Yes, and without much hesitation.

The design is right. The join-not-cascade decision is correct and well-evidenced — it came from an argument I couldn't have had with myself, between two reviewers who checked each other against the actual schema instead of against their own assumptions. The community input was excellent and the agents integrated it faster and more thoroughly than I would have alone.

But I'd hold the document to a harder standard than the debate. **The quality of the discussion and the quality of the artifact are different things**, and I let a good discussion produce a bad artifact because the discussion was genuinely impressive to watch.

Two hours of review produced maybe fifteen minutes of load-bearing decisions. The rest was scope arguments and revision bureaucracy that made the document worse. Next time the rule is simpler: when the decision is made, write the decision. Not the transcript of arriving at it.

---

*OpenBrain is a personal RAG knowledge system — Postgres and pgvector, with an MCP connector so Claude can read and write it. Previous posts: [architecture](/blog/project-openbrain-2-architecture), [the OB2 cutover](/blog/session-ob2-cutover), [retrieval reliability](/blog/project-openbrain-3-retrieval-reliability).*
