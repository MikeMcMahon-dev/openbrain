-- Reclassify technical rows misfiled into Study/OpenBrain by the ingest-taxonomy-override
-- drop bug (see docs/HANDOFF-taxonomy-fix-2026-07-19.md). Scope: Mike's rows since the
-- taxonomy shipped (2026-06-17). Family rows (annie/beth, genuine Study) are left ALONE.
--
-- WORKFLOW: run #1 (read-only) and eyeball. Fill the id-lists in #2 from the audit. Run #2
-- inside a transaction, verify the counts, then COMMIT (or ROLLBACK). Run #3 to purge probes.
-- Do NOT blind-UPDATE off the regex — it is a *finder*, not a classifier.

-- ── 1) AUDIT (read-only) ────────────────────────────────────────────────────────
SELECT id, domain, environment, system, tags, created_at, left(content, 140) AS preview
FROM public.knowledge
WHERE created_at >= '2026-06-17'
  AND status = 'current'
  AND domain IN ('Study', 'OpenBrain')
  AND created_by = 'mike.mcmahon67'
  AND ( content ~* '\m(spectrenet|mcmahon\.home|coredns|technitium|dns-[0-9]|pi-?hole|keepalived|metallb|proxmox|pmx-01|talos|kubectl|k3s|kubernetes|netplan|vlan|qnap|homelab)\M'
        OR tags && ARRAY['Network','K8s','Homelab','SpectreNet','Proxmox'] )
ORDER BY created_at DESC;

-- ── 2) RECLASSIFY (fill id-lists from #1; run in a txn) ─────────────────────────
BEGIN;
  -- k8s/kubernetes/talos/kubectl content → K8s
  UPDATE public.knowledge
     SET domain = 'K8s', environment = 'Production', system = COALESCE(system, 'SpectreNet')
   WHERE id = ANY(ARRAY[]::uuid[]);   -- <-- k8s ids from audit

  -- dns/network/proxmox infra → Network
  UPDATE public.knowledge
     SET domain = 'Network', environment = 'Production', system = COALESCE(system, 'SpectreNet')
   WHERE id = ANY(ARRAY[]::uuid[]);   -- <-- network ids from audit

  -- sanity before commit
  SELECT domain, environment, count(*) FROM public.knowledge
   WHERE created_at >= '2026-06-17' AND created_by = 'mike.mcmahon67'
   GROUP BY 1, 2 ORDER BY 1, 2;
-- COMMIT;   -- uncomment when the counts look right; else ROLLBACK;
ROLLBACK;

-- ── 3) PURGE diagnosis probe rows ───────────────────────────────────────────────
DELETE FROM public.knowledge
 WHERE content LIKE 'TAXPROBE-%'
    OR content LIKE 'WAF direct-path%'
    OR content LIKE 'Direct-path verbatim test%'
    OR content LIKE 'Ingest connectivity probe%'
    OR content LIKE 'Probe two:%';
