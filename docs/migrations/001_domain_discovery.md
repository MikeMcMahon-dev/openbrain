# OB2 Domain Discovery — Stage 1 Output

**Date:** 2026-05-13
**Executed by:** Claude Code (Stage 1 ADR + Domain Discovery)
**Source table:** `public.thoughts` (699 rows as of execution date)

---

## Schema Note — Critical Finding

The design spec's domain discovery queries referenced `subject` and `topic` as direct columns.
They do **not exist as columns**. They are stored inside the `metadata` JSONB field:

```sql
metadata->>'subject'   -- maps to OB2 domain/system
metadata->>'topic'     -- maps to OB2 topic/component tag
metadata->>'owner'     -- maps to created_by
```

The migration script (`scripts/migrate_thoughts.py`) **must** read from `metadata` JSONB,
not from direct columns. The design doc SQL queries must be adapted accordingly.

Actual `public.thoughts` columns:
```
id, content, embedding, metadata (jsonb), created_at, updated_at,
slack_username, tenant_id, visibility, source_type, source_team_id,
source_workspace_id, source_channel_id, created_by_user_id,
created_by_user_login, document_id, chunk_id, source_chunk_id,
source_uri, content_hash, slack_user_id
```

---

## Raw SQL Output

### Q1: Subject/topic combinations (adapted to metadata jsonb)

```
subject                                          | topic                                    | owner              | count | earliest             | latest
-------------------------------------------------|------------------------------------------|--------------------|-------|----------------------|---------------------
engineering                                      | notes                                    | mike.mcmahon67     |   391 | 2026-03-16           | 2026-03-21
project                                          | documentation                            | mike.mcmahon67     |   113 | 2026-03-21           | 2026-03-21
(null)                                           | (null)                                   | (null)             |    23 | 2026-03-14           | 2026-03-25
session-context                                  | session-wrap                             | mike.mcmahon67     |     9 | 2026-03-31           | 2026-04-05
open-brain                                       | adr                                      | mike.mcmahon67     |     5 | 2026-03-31           | 2026-03-31
Home Network                                     | NAS Benchmark                            | mike.mcmahon67     |     4 | 2026-05-06           | 2026-05-08
multi-agent-lab                                  | adr                                      | mike.mcmahon67     |     3 | 2026-03-31           | 2026-03-31
nutrition                                        | food log                                 | mike.mcmahon67     |     3 | 2026-04-14           | 2026-04-14
agent-lab                                        | adr                                      | mike.mcmahon67     |     3 | 2026-03-31           | 2026-03-31
Mike                                             | personal-history                         | mike.mcmahon67     |     2 | 2026-03-21           | 2026-03-21
SpectreNet                                       | Session Handoff                          | mike.mcmahon67     |     2 | 2026-05-05           | 2026-05-05
food-log                                         | 2026-04-14                               | mike.mcmahon67     |     2 | 2026-04-15           | 2026-04-15
docx_url_smoke_test                              | docx_url_smoke                           | mike.mcmahon67     |     2 | 2026-03-31           | 2026-03-31
Mike                                             | resume                                   | mike.mcmahon67     |     2 | 2026-03-21           | 2026-03-21
Home Network                                     | PMX-01 Network State                     | mike.mcmahon67     |     2 | 2026-05-06           | 2026-05-08
Mike                                             | career                                   | mike.mcmahon67     |     2 | 2026-03-21           | 2026-03-21
nutrition                                        | daily food log April 13 2026             | mike.mcmahon67     |     2 | 2026-04-13           | 2026-04-14
OpenBrain                                        | architecture                             | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
openbrain                                        | session_notes_2026-04-01                 | mike.mcmahon67     |     1 | 2026-04-01           | 2026-04-01
live smoke test note                             | 2026-03-30                               | tenant-a-owner     |     1 | 2026-03-30           | 2026-03-30
Personal                                         | Betrayal Wound                           | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
Homelab                                          | Proxmox Inventory                        | mike.mcmahon67     |     1 | 2026-03-25           | 2026-03-25
[malformed subject: PR smoke check message]      | ops                                      | mike.mcmahon67     |     1 | 2026-03-27           | 2026-03-27
Home Network                                     | SpectreNet Handoff                       | mike.mcmahon67     |     1 | 2026-05-05           | 2026-05-05
Science                                          | Cell Division Final Test Prep            | anneliesepaige     |     1 | 2026-04-12           | 2026-04-12
engineering                                      | multi-agent-patterns                     | mike.mcmahon67     |     1 | 2026-04-01           | 2026-04-01
Science                                          | Detailed Study Plan Annie                | anneliesepaige     |     1 | 2026-04-11           | 2026-04-11
Home Network                                     | DHCP Migration                           | mike.mcmahon67     |     1 | 2026-05-05           | 2026-05-05
live smoke test note                             | 2026-04-01                               | tenant-a-owner     |     1 | 2026-04-01           | 2026-04-01
agent-lab                                        | project-instructions                     | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
Health                                           | Weight Loss Log April 2026               | mike.mcmahon67     |     1 | 2026-04-13           | 2026-04-13
portfolio-blog                                   | ci-cd-pipeline-implementation            | mike.mcmahon67     |     1 | 2026-04-06           | 2026-04-06
Science                                          | Cell Division Adaptive Mastery Tree v2   | anneliesepaige     |     1 | 2026-04-12           | 2026-04-12
Science                                          | Classification Review and Study Guide    | anneliesepaige     |     1 | 2026-03-27           | 2026-03-27
mike                                             | user_profile core context manual_seed    | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
Biology                                          | Meiosis coloring worksheet pages 6-7     | anneliesepaige     |     1 | 2026-04-01           | 2026-04-01
smoke test                                       | 2026-04-02                               | mike.mcmahon67     |     1 | 2026-04-02           | 2026-04-02
portfolio-blog                                   | session-dynamic-clippy-recent-posts      | mike.mcmahon67     |     1 | 2026-04-07           | 2026-04-07
smoke test                                       | 2026-03-29                               | mike.mcmahon67     |     1 | 2026-03-29           | 2026-03-29
Geometry                                         | Q4 Geometry Assessment (pages 4-6)       | anneliesepaige     |     1 | 2026-03-31           | 2026-03-31
Personal                                         | Betrayal                                 | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
Personal                                         | Mike McMahon history                     | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
security                                         | security-hardening-2026-05-06            | mike.mcmahon67     |     1 | 2026-05-07           | 2026-05-07
Science                                          | Mini Test Classification Results         | anneliesepaige     |     1 | 2026-03-29           | 2026-03-29
mike                                             | user_profile full context manual_seed    | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
live smoke test note                             | 2026-03-31                               | tenant-a-owner     |     1 | 2026-03-31           | 2026-03-31
openbrain                                        | session_closeout_2026-03-31              | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
learning behavior                                | study habits                             | anneliesepaige     |     1 | 2026-03-30           | 2026-03-30
Kubernetes                                       | MetalLB kube-proxy nftables K8s 1.35     | mike.mcmahon67     |     1 | 2026-05-07           | 2026-05-07
Biology                                          | Mitosis worksheets pages 1-3             | anneliesepaige     |     1 | 2026-04-01           | 2026-04-01
Annie                                            | family-context                           | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
[malformed subject: session summary string]      | session-notes                            | mike.mcmahon67     |     1 | 2026-04-01           | 2026-04-01
home-lab                                         | project-instructions                     | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
Science                                          | Taxonomy and Classification Study        | anneliesepaige     |     1 | 2026-03-27           | 2026-03-27
Database Security Architecture                   | portfolio-blog email subscription        | mike.mcmahon67     |     1 | 2026-04-05           | 2026-04-05
smoke test                                       | 2026-05-01                               | mike.mcmahon67     |     1 | 2026-05-01           | 2026-05-01
Mike                                             | family-context                           | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
live smoke test note                             | 2026-05-01                               | tenant-a-owner     |     1 | 2026-05-01           | 2026-05-01
4th Quarter Science                              | Biology Classification                   | anneliesepaige     |     1 | 2026-03-21           | 2026-03-21
Kubernetes                                       | CKA Training Plan                        | mike.mcmahon67     |     1 | 2026-03-25           | 2026-03-25
Career                                           | Technical Interview Positioning          | mike.mcmahon67     |     1 | 2026-05-06           | 2026-05-06
4th Quarter Science                              | Mitosis                                  | anneliesepaige     |     1 | 2026-03-21           | 2026-03-21
Science                                          | Classification, Cladograms, Keys         | anneliesepaige     |     1 | 2026-03-27           | 2026-03-27
Science                                          | Cellular Division Study Guide (p1-2)     | anneliesepaige     |     1 | 2026-04-11           | 2026-04-11
AI Engineering                                   | portfolio progress                       | mike.mcmahon67     |     1 | 2026-03-30           | 2026-03-30
Geometry                                         | Q4 Geometry Assessment (pages 17-20)     | anneliesepaige     |     1 | 2026-03-31           | 2026-03-31
AI Engineering                                   | velocity benchmark                       | mike.mcmahon67     |     1 | 2026-03-30           | 2026-03-30
Engineering Philosophy                           | pragmatic-programmer                     | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
Beth                                             | family-context                           | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
Geometry                                         | Q4 Geometry Assessment (pages 9-10)      | anneliesepaige     |     1 | 2026-03-31           | 2026-03-31
career                                           | DESI commercialize decision              | mike.mcmahon67     |     1 | 2026-03-30           | 2026-03-30
AI Development                                   | Multi-Agent Infrastructure & OpenBrain   | mike.mcmahon67     |     1 | 2026-05-06           | 2026-05-06
live smoke test note                             | 2026-05-02                               | tenant-a-owner     |     1 | 2026-05-02           | 2026-05-02
Science                                          | Cellular Division Study Guide (p2)       | anneliesepaige     |     1 | 2026-04-11           | 2026-04-11
Science                                          | Cell Division Session Report Final       | anneliesepaige     |     1 | 2026-04-12           | 2026-04-12
Mike                                             | values                                   | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
AI Engineering                                   | project documentation                    | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
live smoke test note                             | 2026-05-03                               | tenant-a-owner     |     1 | 2026-05-03           | 2026-05-03
home-lab repo structure and file locations       | repo organization                        | mike.mcmahon67     |     1 | 2026-04-05           | 2026-04-05
Geometry                                         | Q4 Assessment Practice                   | anneliesepaige     |     1 | 2026-03-31           | 2026-03-31
Geometry                                         | Q4 Geometry Assessment (pages 11-13)     | anneliesepaige     |     1 | 2026-03-31           | 2026-03-31
Biology                                          | Mitosis vs meiosis + amoeba sisters      | anneliesepaige     |     1 | 2026-04-01           | 2026-04-01
cka-study                                        | services-networking-session-1            | mike.mcmahon67     |     1 | 2026-04-28           | 2026-04-28
Engineering Philosophy                           | programmer-growth-stages                 | mike.mcmahon67     |     1 | 2026-03-22           | 2026-03-22
Geometry                                         | Q4 Geometry Assessment (pages 7-8)       | anneliesepaige     |     1 | 2026-03-31           | 2026-03-31
live smoke test note                             | 2026-05-14                               | tenant-a-owner     |     1 | 2026-05-14           | 2026-05-14
SpectreNet                                       | Infrastructure IPs                       | mike.mcmahon67     |     1 | 2026-05-05           | 2026-05-05
openbrain                                        | pdf_ingest_implementation_todo           | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
Geometry                                         | Q4 Geometry Assessment (pages 1-3)       | anneliesepaige     |     1 | 2026-03-31           | 2026-03-31
Personal                                         | Mike McMahon - Core Wound Session 2      | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
OpenBrain                                        | text-ingest-fix-verification             | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
Home Network                                     | SpectreNet                               | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
Mike McMahon SRE Resume 2025                     | resume                                   | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
Biology                                          | handwritten notes page 1 of 1            | anneliesepaige     |     1 | 2026-04-02           | 2026-04-02
OpenBrain                                        | operations                               | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
Mike                                             | personal-context                         | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
Personal                                         | Mike McMahon - Core Wound Session 4      | mike.mcmahon67     |     1 | 2026-05-04           | 2026-05-04
[malformed subject: smoke check string]          | ops                                      | mike.mcmahon67     |     1 | 2026-03-27           | 2026-03-27
nutrition                                        | daily food log April 13 2026 snack added | mike.mcmahon67     |     1 | 2026-04-13           | 2026-04-13
mcp_smoke_test                                   | smoke_2026_05_01                         | mike.mcmahon67     |     1 | 2026-05-01           | 2026-05-01
Personal - Mental Health                         | Betrayal Wound                           | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
open-brain                                       | project-instructions                     | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
pdf_smoke_test                                   | pdf_smoke                                | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
Personal                                         | Mike McMahon - Core Wound Session 1      | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
mike                                             | user_context                             | mike.mcmahon67     |     1 | 2026-04-01           | 2026-04-01
Personal                                         | Betrayal Wound - Session 1               | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
Personal                                         | Mike McMahon - Core Wound Session 3      | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
Science                                          | Targeted Review Practice Session         | anneliesepaige     |     1 | 2026-03-29           | 2026-03-29
nutrition                                        | daily food log April 13 2026 updated     | mike.mcmahon67     |     1 | 2026-04-13           | 2026-04-13
[malformed subject: RRF smoke check string]      | ops                                      | mike.mcmahon67     |     1 | 2026-03-27           | 2026-03-27
openbrain                                        | docx_url_ingest_implementation           | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
smoke test                                       | 2026-03-31                               | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
smoke test                                       | 2026-03-30                               | mike.mcmahon67     |     1 | 2026-03-30           | 2026-03-30
OpenBrain                                        | session-summary-2026-03-21               | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
System                                           | Test                                     | mike.mcmahon67     |     1 | 2026-05-03           | 2026-05-03
Science                                          | Advanced Classification Practice         | anneliesepaige     |     1 | 2026-03-28           | 2026-03-28
Science                                          | Final Boss Classification Session        | anneliesepaige     |     1 | 2026-04-01           | 2026-04-01
Mike                                             | life-goals                               | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
portfolio-blog                                   | ai-assistant-reliability-and-trust       | mike.mcmahon67     |     1 | 2026-04-08           | 2026-04-08
Math                                             | Geometry test prep                       | anneliesepaige     |     1 | 2026-03-27           | 2026-03-27
CKA Training                                     | CKA Phase 1 Lab Session 2026-04-04       | mike.mcmahon67     |     1 | 2026-04-04           | 2026-04-04
Career                                           | Interview Stories Bank                   | mike.mcmahon67     |     1 | 2026-05-06           | 2026-05-06
Health                                           | weight loss project log                  | mike.mcmahon67     |     1 | 2026-04-12           | 2026-04-12
openbrain                                        | mcp-implementation-2026-05-01            | mike.mcmahon67     |     1 | 2026-05-01           | 2026-05-01
OpenBrain                                        | development                              | mike.mcmahon67     |     1 | 2026-03-21           | 2026-03-21
Geometry                                         | Q4 Geometry Assessment (pages 14-16)     | anneliesepaige     |     1 | 2026-03-31           | 2026-03-31
[malformed subject: week 3A session string]      | session work                             | mike.mcmahon67     |     1 | 2026-04-05           | 2026-04-05
security                                         | evaluation_guidance                      | mike.mcmahon67     |     1 | 2026-04-01           | 2026-04-01
Science                                          | Classification and Kingdoms              | anneliesepaige     |     1 | 2026-03-27           | 2026-03-27
Claude Working Sessions                          | portfolio-blog                           | mike.mcmahon67     |     1 | 2026-04-02           | 2026-04-02
kubernetes                                       | ingress_lab_progress                     | mike.mcmahon67     |     1 | 2026-04-04           | 2026-04-04
System Preference                                | Study Session Logging Rule               | anneliesepaige     |     1 | 2026-03-27           | 2026-03-27
openbrain                                        | pdf_ingest_test_harness                  | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
live smoke test note                             | 2026-04-03                               | tenant-a-owner     |     1 | 2026-04-03           | 2026-04-03
Claude Working Sessions                          | agent-eval-harness                       | mike.mcmahon67     |     1 | 2026-04-02           | 2026-04-02
4th Quarter Science                              | DNA                                      | anneliesepaige     |     1 | 2026-03-21           | 2026-03-21
[malformed subject: corrected misconception]     | 2026-04-11                               | anneliesepaige     |     1 | 2026-04-11           | 2026-04-11
live smoke test note                             | 2026-03-29                               | tenant-a-owner     |     1 | 2026-03-29           | 2026-03-29
architecture                                     | ingest-pipeline-evolution                | mike.mcmahon67     |     1 | 2026-04-02           | 2026-04-02
multi-agent-lab                                  | project-instructions                     | mike.mcmahon67     |     1 | 2026-03-31           | 2026-03-31
health                                           | food log                                 | mike.mcmahon67     |     1 | 2026-04-13           | 2026-04-13
portfolio-blog                                   | session-keeping-production-running       | mike.mcmahon67     |     1 | 2026-04-05           | 2026-04-05
session-context                                  | context-mode-deployment                  | mike.mcmahon67     |     1 | 2026-04-11           | 2026-04-11
Career                                           | Resume and Career Context                | mike.mcmahon67     |     1 | 2026-05-06           | 2026-05-06
preferences                                      | food logging workflow                    | mike.mcmahon67     |     1 | 2026-04-14           | 2026-04-14
Science                                          | Study Plan Annie Science Final           | anneliesepaige     |     1 | 2026-04-11           | 2026-04-11
```

### Q2: Rows with null subject AND null topic

```
null_taxonomy_count: 23
```
These are Slack messages (source_type='slack') with no metadata taxonomy. Default mapping: Study/Study.

### Q3: Row count by source_type

```
obsidian        | 391
text            | 172
project_docs    | 113
slack           |  15
(null)          |   8
```

### Q3: Row count by owner

```
mike.mcmahon67  | 645
anneliesepaige  |  35
tenant-a-owner  |   9   (smoke test artifact owner)
(null)          |   8
snapple01       |   2
```

---

## Mapping

**Human review required before Stage 2.** Edit the YAML block below. Do not delete unknown
entries — leave them with the default Study/Study mapping. Stage 2's `load_domain_mapping()`
parses only the `mapping:` block. Keys are the exact `metadata->>'subject'` string value.

Notes on notable entries:
- `engineering / notes` (391 rows): Obsidian vault import — broad SRE/K8s/Terraform/networking
  reference notes. All historical on migration — do NOT auto-promote to current.
- `project / documentation` (113 rows): OpenBrain project docs ingested 2026-03-21.
- `(null)` (23 rows): Slack messages — no subject/topic. Default Study/Study.
- `tenant-a-owner` rows (9): smoke test artifacts. Map to OpenBrain/Study.
- Malformed subjects (PR smoke check strings used as subject): map to OpenBrain/Study.
- `Personal` / `Personal - Mental Health` rows: sensitive personal content, owner=mike.mcmahon67.

```yaml
mapping:
  # ── High-volume buckets (review carefully) ──────────────────────────────────

  # engineering/notes (391 rows) — PATH-BASED CLASSIFICATION REQUIRED.
  # subject='engineering' + topic='notes' is insufficient — sub-classify by source_uri prefix.
  # load_domain_mapping() must return _path_rules for this key.
  # classify_row() checks source_uri against prefixes IN ORDER (first match wins).
  # NOTE: source_channel="text" (no file path) rule covers future text-ingest operational notes
  #       only — zero existing rows have source_type='text' in this bucket today.
  "engineering":
    _path_rules:
      - prefix: "vault/Homelab Notes/"
        domain: "Network"
        environment: "Lab"
        system: null
        tags: ["Network", "Lab", "Homelab"]
        count: 11
      - prefix: "vault/Infrastructure as Code Notes/NV Prep/"
        domain: "Study"
        environment: "Study"
        system: null
        tags: ["Study", "NV-Prep"]
        count: 72
      - prefix: "vault/Infrastructure as Code Notes/Bare-Metal/"
        domain: "Study"
        environment: "Lab"
        system: null
        tags: ["Study", "Bare-Metal"]
        count: 16
      - prefix: "vault/Infrastructure as Code Notes/"
        domain: "Study"
        environment: "Study"
        system: null
        tags: ["Study", "IaC"]
        count: 279
      - prefix: "vault/AI Study/"
        domain: "Study"
        environment: "Study"
        system: null
        tags: ["Study", "AI"]
        count: 13
    _text_source_rule:
      note: "source_type=text with no file path — future operational state notes ingested from chat. Zero existing rows."
      domain: "Network"
      environment: "Production"
      system: null
      tags: ["Network", "Production"]
    _default:
      domain: "Study"
      environment: "Study"
      system: null
      tags: ["Study", "Engineering"]
      notes: "Fallback if no prefix matches. Should not occur for existing rows — investigate if hit."

  "project":
    domain: "OpenBrain"
    environment: "Study"
    system: null
    tags: ["ProjectDocs"]
    notes: "113 rows — OpenBrain project documentation ingested 2026-03-21."

  # ── Network / Production state ────────────────────────────────────────────
  "Home Network":
    domain: "Network"
    environment: "Production"
    system: "SpectreNet"
    tags: ["Network", "Production"]

  "SpectreNet":
    domain: "Network"
    environment: "Production"
    system: "SpectreNet"
    tags: ["Network", "Production"]

  "Homelab":
    domain: "Network"
    environment: "Production"
    system: "PMX-01"
    tags: ["Network", "Production", "Proxmox"]

  # ── Kubernetes ───────────────────────────────────────────────────────────
  "kubernetes":
    domain: "K8s"
    environment: "Lab"
    system: null
    tags: ["K8s", "Lab"]

  "Kubernetes":
    domain: "K8s"
    environment: "Lab"
    system: null
    tags: ["K8s"]

  "CKA Training":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["K8s", "CKA"]

  "cka-study":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["K8s", "CKA"]

  # ── OpenBrain system ─────────────────────────────────────────────────────
  "open-brain":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["OpenBrain"]

  "openbrain":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["OpenBrain"]

  "OpenBrain":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["OpenBrain"]

  "architecture":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["OpenBrain", "Architecture"]

  "session-context":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["OpenBrain", "Session"]

  # ── Agent lab projects ───────────────────────────────────────────────────
  "agent-lab":
    domain: "OpenBrain"
    environment: "Study"
    system: null
    tags: ["AgentLab"]

  "multi-agent-lab":
    domain: "OpenBrain"
    environment: "Study"
    system: null
    tags: ["MultiAgentLab"]

  "home-lab":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["HomeLab", "Reference"]

  # ── Personal ─────────────────────────────────────────────────────────────
  "Mike":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "Mike"]

  "mike":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "Mike"]

  "Personal":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal"]

  "Personal - Mental Health":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "MentalHealth"]

  "Beth":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "Family"]

  "Annie":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "Family"]

  # ── Career ───────────────────────────────────────────────────────────────
  "Career":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Career"]

  "career":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Career"]

  # ── Study — Annie school subjects ────────────────────────────────────────
  "Science":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Science", "Annie"]

  "Biology":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Biology", "Annie"]

  "Geometry":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Geometry", "Annie"]

  "Math":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Math", "Annie"]

  "4th Quarter Science":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Science", "Annie"]

  "learning behavior":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Study", "Annie"]

  "System Preference":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Study", "Annie"]

  # ── Study — Mike engineering study ───────────────────────────────────────
  "Engineering Philosophy":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Engineering", "Reference"]

  "AI Engineering":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["AI", "Engineering"]

  "AI Development":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["AI", "Engineering"]

  "Database Security Architecture":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Security", "Architecture"]

  # ── Security ─────────────────────────────────────────────────────────────
  "security":
    domain: "Security"
    environment: "Study"
    system: null
    tags: ["Security"]

  # ── Health / Nutrition ───────────────────────────────────────────────────
  "nutrition":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "Health", "Nutrition"]

  "health":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "Health"]

  "Health":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "Health"]

  "food-log":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "Health", "FoodLog"]

  "preferences":
    domain: "Personal"
    environment: "Study"
    system: null
    tags: ["Personal", "Preferences"]

  # ── Portfolio blog ───────────────────────────────────────────────────────
  "portfolio-blog":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["PortfolioBlog"]

  "Claude Working Sessions":
    domain: "Study"
    environment: "Study"
    system: null
    tags: ["Sessions"]

  # ── Smoke tests / test artifacts ─────────────────────────────────────────
  "live smoke test note":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["SmokeTest"]

  "smoke test":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["SmokeTest"]

  "mcp_smoke_test":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["SmokeTest"]

  "pdf_smoke_test":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["SmokeTest"]

  "docx_url_smoke_test":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["SmokeTest"]

  "System":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["SmokeTest"]

  # ── Malformed subjects (used full sentence as subject key) ───────────────
  # These will be matched by exact string in load_domain_mapping().
  # Strings truncated here for readability — migration script must use full exact strings.
  # All default to OpenBrain/Study since they are operational notes.
  "__malformed__":
    domain: "OpenBrain"
    environment: "Study"
    system: "OpenBrain"
    tags: ["Ops"]
    notes: >
      Fallback for subjects that contain full sentences (PR smoke check messages, etc.).
      Migration script should detect these by length > 80 chars and apply this mapping.

  # ── Default (unknown or null subject) ────────────────────────────────────
  "__default__":
    domain: "Study"
    environment: "Study"
    system: null
    tags: []
    notes: "Applied when subject is null or not found in this mapping. 23 null-taxonomy rows."
```

---

## Migration Notes for Stage 2

1. `load_domain_mapping()` parses the `mapping:` YAML block above. Key = exact subject string.
2. Null subject rows → `__default__` mapping.
3. Subject strings > 80 chars → `__malformed__` mapping (heuristic for sentences-as-subjects).
4. All migrated records use `status='historical'`. No auto-promotion to `current`.
5. `ingest_id` in the new schema maps from `source_chunk_id` or `content_hash` in thoughts.
6. `created_by` maps from `metadata->>'owner'` (preferred) then `created_by_user_login`.
7. `source` = `"migration:thoughts:2026-05-13"` for all migrated rows.
8. Verify: `COUNT(knowledge) == COUNT(thoughts)` before closing Stage 2.

### Path-based classification (engineering bucket)

When `mapping[subject]` contains a `_path_rules` key, `classify_row()` must apply
path-based sub-classification instead of the flat domain/environment values:

```python
def classify_row(subject, topic, source_uri, source_type, mapping):
    entry = mapping.get(subject) or mapping["__default__"]

    if "_path_rules" in entry:
        # Text-source rule checked first (no file path)
        if source_type == "text" and not source_uri:
            return entry["_text_source_rule"]
        # Path prefix rules — first match wins
        if source_uri:
            for rule in entry["_path_rules"]:
                if rule["prefix"] in source_uri:
                    return rule
        # Fallback
        return entry["_default"]

    return entry  # flat mapping — use directly
```

`classify_row()` signature must accept `source_uri` and `source_type` (in addition to
`subject` and `topic`) since the engineering bucket cannot be classified from subject alone.

**Human sign-off required on mapping before `--execute`.**
