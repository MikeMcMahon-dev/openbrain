# OpenBrain Taxonomy Drift Audit — public.knowledge

_Generated: 2026-06-18 02:57 UTC (READ-ONLY; SELECT only)_

Source of truth: `api/taxonomy_map.py` (CANONICAL_TAGS, TAG_ALIASES, normalize_tags). ADR-012.

## Corpus

- Total rows: **699**
- Rows carrying >=1 tag: **669**
- Distinct tags in use: **45**
- Canonical vocabulary size: 49 (+13 alias keys)

## 1 + 2. Tag classification (via normalize_tags)

- Canonical: **41** distinct
- Aliased / casing-folded: **3** distinct
- Retired (drop alias, still present): **1** distinct
- Unknown (drift): **0** distinct

### Canonical tags (in use)

| tag | rows |
|---|---|
| `IaC` | 367 |
| `RedHat` | 127 |
| `ProjectDocs` | 113 |
| `NV-Prep` | 72 |
| `Bash` | 63 |
| `Ansible` | 38 |
| `Personal` | 37 |
| `Annie` | 33 |
| `OpenBrain` | 28 |
| `Terraform` | 27 |
| `Science` | 18 |
| `AI` | 17 |
| `Bare-Metal` | 16 |
| `Network` | 14 |
| `Production` | 14 |
| `Mike` | 13 |
| `Health` | 12 |
| `Homelab` | 11 |
| `Session` | 10 |
| `Geometry` | 8 |
| `Nutrition` | 7 |
| `Engineering` | 6 |
| `K8s` | 5 |
| `AgentLab` | 4 |
| `Biology` | 4 |
| `Career` | 4 |
| `MultiAgentLab` | 4 |
| `PortfolioBlog` | 4 |
| `Reference` | 3 |
| `Security` | 3 |
| `Architecture` | 2 |
| `CKA` | 2 |
| `Family` | 2 |
| `FoodLog` | 2 |
| `Study` | 2 |
| `Lab` | 1 |
| `Math` | 1 |
| `Ops` | 1 |
| `Preferences` | 1 |
| `Proxmox` | 1 |
| `Python` | 1 |

### Aliased / casing-folded tags (would fold on re-normalize)

| tag (as stored) | rows | folds to |
|---|---|---|
| `Sessions` | 2 | `Session` |
| `HomeLab` | 1 | `Homelab` |
| `MentalHealth` | 1 | `Mental-health` |

### Retired tags still present (should be removed)

| tag | rows |
|---|---|
| `SmokeTest` | 19 |

### Unknown tags — DRIFT (not canonical, not aliased)

_none — no drift_

## 3. Near-duplicate detection (casing/typo drift not yet aliased)

_none — no near-duplicate drift detected_

## 4. domain / environment vs allowed enums

Allowed domains: ['K8s', 'Network', 'OpenBrain', 'Personal', 'Security', 'Study']
Allowed environments: ['Archive', 'Lab', 'Production', 'Study']

### domain distribution

| domain | rows | status |
|---|---|---|
| `Study` | 459 | ok |
| `OpenBrain` | 169 | ok |
| `Personal` | 41 | ok |
| `Network` | 25 | ok |
| `K8s` | 3 | ok |
| `Security` | 2 | ok |

### environment distribution

| environment | rows | status |
|---|---|---|
| `Study` | 655 | ok |
| `Lab` | 30 | ok |
| `Production` | 14 | ok |

## 5. system value distribution

| system | rows |
|---|---|
| `<null>` | 638 |
| `OpenBrain` | 48 |
| `SpectreNet` | 12 |
| `PMX-01` | 1 |

## 6. Verdict

- Clean rows: **699** / 699
- Drifted rows (unknown tag or out-of-enum domain/env): **0**
  - rows touched by an unknown tag: 0
  - rows with out-of-enum domain/environment: 0
- Incomplete (NULL domain): 0 | (NULL environment): 0

### Suggested actions

- NORMALIZE: `Sessions` (2 rows) already folds to `Session` via taxonomy_map — re-run retag to persist.
- NORMALIZE: `HomeLab` (1 rows) already folds to `Homelab` via taxonomy_map — re-run retag to persist.
- NORMALIZE: `MentalHealth` (1 rows) already folds to `Mental-health` via taxonomy_map — re-run retag to persist.
- REMOVE: `SmokeTest` (19 rows) is a retired drop-alias — strip from rows.

**Overall: CLEAN**

