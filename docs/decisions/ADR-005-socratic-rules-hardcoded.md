# ADR-005: SOCrATIC_RULES Hardcoded in Python

**Status:** Accepted
**Date:** 2026-03-28

## Context
SafeIngest gate needed to protect against prompt injection via student-submitted content. Question: should tutoring behavior rules live in the database (configurable) or in code?

## Decision
SOCrATIC_RULES are hardcoded in Python in `api/tutor.py`. They are not stored in the database, not user-configurable, and not overridable via any ingest path.

## Consequences
- Injected content cannot override tutoring behavior regardless of SafeIngest gate status
- Two-layer SafeIngest gate: regex pattern ($0.00) → optional Haiku classifier (cost proportional to threat signal)
- OPENBRAIN_EXTENDED_CHECKS=true enables Haiku escalation — off by default
- Gate logs the flag even when allowing content through — parent reporting can surface flagged ingests
- Trade-off: tutoring rules require code deploy to change, not a config update — acceptable given the security model
