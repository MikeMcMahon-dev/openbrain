# OpenBrain 2.0 — Session Startup Protocol

This document is the operational guide for using the OB2 knowledge and wiki API in Claude Code sessions. Follow this protocol at session start to establish current context without raw chunk retrieval.

---

## Session Startup

At the start of any session involving infrastructure, network, or homelab work, query the wiki layer first. These pages are compiled from current knowledge records and return immediately without LLM synthesis overhead.

```bash
# Primary wiki pages — check these first
GET /api/wiki/spectrenet-current-state    # current network topology and device state
GET /api/wiki/pmx01-current-state         # Proxmox cluster state
GET /api/wiki/pending-tasks               # open items and in-progress work
```

Each response includes `is_stale: true/false`. A stale page means a knowledge record in its source set has been updated since the last compilation. You can proceed with a stale page (it was accurate at `generated_at`) or recompile:

```bash
POST /api/compile_wiki
{
  "page_name": "spectrenet-current-state",
  "page_type": "system-state",
  "domain": "Network",
  "system": "SpectreNet"
}
```

### If wiki pages are absent or stale

Fall back to direct knowledge queries:

```bash
GET /api/query_state
{
  "domain": "Network",
  "status": "current",
  "limit": 20
}

GET /api/query_state
{
  "domain": "Network",
  "environment": "Production",
  "limit": 10
}
```

These return records ordered by `valid_from DESC` — most recently updated state first.

---

## Ingesting New State

When operational state changes (device config updated, new service deployed, IP changed), ingest the new state immediately:

```bash
POST /api/ingest_state
{
  "content": "Pi-Hole running on 192.168.110.30 (moved from .100.30 on 2026-05-14). DNS upstream: Cloudflare 1.1.1.1, fallback 1.0.0.1.",
  "domain": "Network",
  "environment": "Production",
  "system": "SpectreNet",
  "tags": ["DNS", "component:pihole"],
  "source": "claude-code-session"
}
```

Required fields: `content`, `domain`, `environment`.

Valid `domain` values: `Network`, `K8s`, `Security`, `Study`, `OpenBrain`, `Personal`

Valid `environment` values: `Production`, `Lab`, `Study`, `Archive`

If a `component:*` tag is included and a current record already exists for the same `system` + `component:*` tag, the insert will return `409 conflict`. Use the supersession workflow instead.

---

## Superseding Stale State

When operational state changes for a component that already has a `current` record:

### Step 1 — Propose

```bash
POST /api/propose_supersession
{
  "supersedes_id": "<id of the current record being replaced>",
  "content": "Pi-Hole now running on 192.168.110.30 (was .100.30). Moved 2026-05-14 to resolve VLAN segment conflict.",
  "domain": "Network",
  "environment": "Production",
  "system": "SpectreNet",
  "tags": ["DNS", "component:pihole"],
  "source": "claude-code-session"
}
```

Response includes `proposal_id`. The new record is created as `status='draft'` — it is not yet queryable as current state.

### Step 2 — Confirm (human)

The confirm step requires explicit human approval. It atomically sets:
- Old record: `status='superseded'`, `valid_until=now()`
- Draft record: `status='current'`, `valid_from=now()`

```bash
POST /api/confirm_supersession
{
  "proposal_id": "<proposal_id from step 1>"
}
```

This is the only write path that modifies existing records. It cannot be triggered by the agent alone.

---

## Finding Records to Supersede

To find the current record ID for a specific component:

```bash
GET /api/query_state
{
  "domain": "Network",
  "system": "SpectreNet",
  "status": "current",
  "limit": 20
}
```

Look for the record with the matching `component:*` tag in its `tags` array.

---

## Domain Reference

| Domain | Covers |
|---|---|
| `Network` | SpectreNet topology, device configs, VLANs, DNS, routing |
| `K8s` | StanzaLab cluster, deployments, services |
| `Security` | Firewall rules, certificates, access controls |
| `Study` | IaC notes, certification prep, reference material |
| `OpenBrain` | This system's own operational state |
| `Personal` | Personal notes, non-technical context |

| Environment | Meaning |
|---|---|
| `Production` | Live infrastructure, authoritative state |
| `Lab` | Homelab experimental, non-authoritative |
| `Study` | Reference/learning material |
| `Archive` | Historical, no longer active |

---

## Wiki Page Naming Convention

Use lowercase kebab-case. Current standard pages:

| Page name | Content |
|---|---|
| `spectrenet-current-state` | Full network topology, device list, IP assignments |
| `pmx01-current-state` | Proxmox node state, VMs, LXCs |
| `stanzalab-current-state` | K8s cluster state |
| `pending-tasks` | Open work items across all domains |
| `openbrain-current-state` | OpenBrain system state and configuration |

Compile a page when you have a coherent set of current records and want a persistent synthesized artifact. Do not compile mid-burst — wait until all ingests for a session are complete.

---

## Status Reference

| Status | Meaning | Queryable by default? |
|---|---|---|
| `current` | Authoritative, present-tense | Yes |
| `superseded` | Replaced; `valid_until` is set | No |
| `historical` | Migrated from `thoughts`; not reviewed for currency | No |
| `draft` | Staged, pending `confirm_supersession` | No |

`GET /api/query_state` defaults to `status=current`. Pass an explicit `status` to query other states.
