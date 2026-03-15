# ChatGPT Connector Plan

This document defines the connector layer needed to use OpenBrain from ChatGPT
personalization (Custom GPT action or actions-like tool flow).

## Objective

- Connect user-facing ChatGPT interaction to:
  - `POST /api/query`
  - `POST /api/generate_quiz`
  - `POST /api/generate_flashcards`
  - `POST /api/ingest` (admin/import workflows only)
- Preserve tenant/user isolation by mapping chat identity into request headers.

## Recommended Tool Layer

Expose a small stable action base URL in front of Vercel, for example:

- `https://openbrain-rouge.vercel.app/api/query`
- `https://openbrain-rouge.vercel.app/api/generate_quiz`
- `https://openbrain-rouge.vercel.app/api/generate_flashcards`
- `https://openbrain-rouge.vercel.app/api/ingest`

Recommended tools (Custom GPT/function style):

- `openbrain_query`
  - Required args: `query`
  - Optional args: `n_results`, `mode`, `student_attempt`
  - Returns: same contract as `/api/query`
- `openbrain_generate_quiz`
  - Required args: `query`
  - Optional args: `n_results`
  - Returns: same contract as `/api/generate_quiz`
- `openbrain_generate_flashcards`
  - Required args: `query`
  - Optional args: `n_results`
  - Returns: same contract as `/api/generate_flashcards`
- `openbrain_ingest`
  - Required args: `source_type`, `source`
  - Optional args: `subject`, `topic`, `sources`

## Identity Mapping (Required)

When a chat call arrives, map chat identity into stable headers:

- `x-openbrain-owner` (chat user/login or family member handle)
- `x-openbrain-tenant-id` (family/team namespace)
- `x-openbrain-user-id` (optional, when available)

Do not trust body fields like `owner` for scope enforcement.

## Error Hygiene for Chat

- Reject bad payloads with clear `400` errors before downstream calls.
- Return deterministic JSON shape even on validation failures:
  - `error`, `message`, `status`
- Keep verbose error text out of user-facing prompt content.

## Validation Checklist (before rollout)

1. Smoke test each tool from raw API calls.
2. Verify ChatGPT response rendering shows `status`, `results`, and `rules`.
3. Verify cross-user calls do not leak tenant data by forcing different owner headers.
4. Add a small smoke case for `openbrain_ingest` with `sources` batching.
