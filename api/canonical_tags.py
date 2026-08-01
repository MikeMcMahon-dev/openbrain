"""Canonical tag seed for OpenBrain taxonomy governance (ADR-012).

THIS FILE IS THE BOOTSTRAP SEED, NOT THE RUNTIME AUTHORITY. The live ingest path reads
the DB table `public.tag_vocabulary`, which is the runtime source of truth and is extended
by `scripts/tag_review.py --approve`. This seed:
  - seeds a fresh DB (supabase/migrations/003_tag_vocabulary.sql is generated from it),
  - is the fallback when the DB is unreachable (cold start, unit tests).

It is allowed to lag the DB without affecting correctness. `tag_review.py --approve`
appends approved tags here (best-effort, then a human commits via PR); the nightly audit
(scripts/audit_taxonomy.py) flags any DB-vs-seed gap from out-of-band changes.

Format: a flat list, one tag per line, so appends and regeneration are trivial and never
require parsing a Python set literal. Edit by intent, not by producer guess.
"""

# fmt: off
CANONICAL_TAGS: list[str] = [
    # Tech / skills (Stage-2 content tags — the valuable searchable facets)
    "IaC", "Terraform", "Ansible", "Bash", "Python", "RedHat", "Bare-Metal",
    "Proxmox", "K8s", "CKA", "Network", "Security", "Architecture", "AI",
    "Engineering", "Reference", "Ops", "Lab", "Production",
    # Systems / projects
    "OpenBrain", "Homelab", "SpectreNet", "PMX-01", "ProjectDocs", "AgentLab",
    "MultiAgentLab", "PortfolioBlog", "Session",
    # People / personal
    "Personal", "Mike", "Beth", "Annie", "Family",
    # Study subjects
    "Science", "Biology", "Geometry", "Math", "Study", "Linux",
    # Life
    "Health", "Nutrition", "FoodLog", "Preferences", "Mental-health",
    # Career / interview (cross-cutting)
    "Career", "Interview", "NV-Prep", "Testing", "Ubuntu Study", "Schooling",
    "Monitoring",
    "LabInfra",
    # Lifecycle flag (not a topic). INTERIM: exempts a row from the P1 recency net
    # (ADR-018). To be REMOVED and promoted to a validated `durable` column in P2, the
    # moment durability gates anything destructive. Lowercase to mark it distinct from
    # the TitleCase topic tags.
    "durable",
    # ── approved via scripts/tag_review.py append below this line ──
]
# fmt: on
