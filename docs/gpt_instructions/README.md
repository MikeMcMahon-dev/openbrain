# Custom GPT instructions — moved to a private repo

The per-user Custom GPT system prompts (one per owner in `OPENBRAIN_TOKEN_OWNER_MAP`) are no
longer kept in this public repository. They contain account details and, for the student
tutor, the parameters of the tutoring-integrity monitor, none of which belong in public.

Source of truth: the private `home-lab` repo, `openbrain-private/gpt_instructions/`.

Same rule for the integrity monitor's rules: the `INTEGRITY_RULES` env var is set from
`openbrain-private/integrity_rules.json`. The mechanism lives here (`api/integrity.py`);
the patterns, thresholds and monitored owners do not.

Applying a change to a Custom GPT is manual: paste the System Prompt into the GPT editor,
re-import `docs/CUSTOM_GPT_ACTION_SPEC.yaml` if the Action schema changed, then save.
