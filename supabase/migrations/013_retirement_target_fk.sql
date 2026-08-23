-- OB2 Migration 013: drop retirement_requests.target_id -> knowledge(id) FK
--
-- WHY: the airlock's `delete` method could never execute. `cmd_execute` deletes the target row
-- while the approved request still references it, so Postgres raises
--   ForeignKeyViolation: ... violates foreign key constraint "retirement_requests_target_id_fkey"
-- and the removal aborts. Found 2026-08-23 by driving the airlock end to end for the first time;
-- the table shipped in 012 with 0 rows and the delete path had never been run.
--
-- 012's comment called the FK intentional ("a target cannot be hard-deleted out from under a
-- pending request without this surfacing"). That intent is sound but it is in direct conflict
-- with the feature the column exists to serve: deleting the target IS the operation. The guard
-- also already lives where it belongs — `_recheck` calls `collect_evidence`, which returns {} for
-- a missing row and refuses with "target no longer exists". Enforcing it a second time in the
-- schema does not add safety; it removes the capability.
--
-- WHY NOT ON DELETE CASCADE: that deletes the request row along with its target, destroying the
-- audit record of the removal at the exact moment it becomes the only evidence the removal
-- happened. The point of the airlock is that removals are accountable.
--
-- target_id stays UUID NOT NULL — a historical reference, not a live pointer. It remains
-- meaningful after the target is gone because `evidence` snapshots title, taxonomy, ref counts,
-- and content_len at request time.
--
-- DEPENDENCY CHECK (ADR-020): no index, view, trigger, policy, or code path depends on this
-- constraint. Its name appears nowhere in the repo — `propose_retirement` catches only
-- `one_open_request_per_target`. The partial unique index and both partial indexes from 012 are
-- on (target_id, method)/(requested_at) and are unaffected by dropping a FK.
--
-- ROLLBACK:
--   ALTER TABLE public.retirement_requests
--     ADD CONSTRAINT retirement_requests_target_id_fkey
--     FOREIGN KEY (target_id) REFERENCES public.knowledge(id);
--   (Only valid while every target_id still resolves — after any executed delete it will not.)

ALTER TABLE public.retirement_requests
  DROP CONSTRAINT IF EXISTS retirement_requests_target_id_fkey;

COMMENT ON COLUMN public.retirement_requests.target_id IS
  'The knowledge row this request concerns. Deliberately NOT a foreign key: an executed delete '
  'removes the target while this audit record must survive it. Resolve against public.knowledge '
  'only for pending/approved requests; for executed ones read the evidence snapshot instead.';
