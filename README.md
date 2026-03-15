# openbrain

## Development checks (before review)

Run these commands before code review so Python + Markdown lint issues are surfaced early:

1) Install development tooling:

```bash
pip install -r requirements-dev.txt
```

The markdown ruleset used by linter is:

- `.pymarkdownlnt.json`

```bash
python -m pymarkdown --disable-rules MD025,MD022,MD029,MD013 --config .pymarkdownlnt.json scan README.md docs/AGENTS.md docs/OPENBRAIN_NEXT_STEPS.md docs/OPENBRAIN_ARCHITECTURE.md
```

2) Run lint:

```bash
make lint
```

That runs:

- Python syntax compile check
- Ruff Python lint + formatting check
- Markdown lint via `pymarkdown` using `.pymarkdownlnt.json`

3) Run preflight smoke checks:

```bash
make smoke
```

This validates handler import and core API routes locally before pushing. For a live deployment smoke pass:

```bash
make smoke-live SMOKE_URL=https://<project-domain>.vercel.app
```

Run both lint and smoke together before deployment:

```bash
make check
```

Capture logs for easy review:

```bash
make check-log
```

```bash
make smoke-live-log SMOKE_URL=https://<project-domain>.vercel.app
```

## Repository hygiene

- `requirements-full.txt` includes optional tooling for local ingestion experiments.
- If generated local artifacts are created during manual experiments, do not commit them to git.
- If local artifacts are modified by ingestion or CLI testing, restore from HEAD:

```bash
git restore -- <artifact_path>
```

## Dependency profile for deployment vs local use

- Vercel API functions use a minimal dependency set in `requirements.txt` to stay within Lambda install limits.
- Local/CLI development using full model tooling can use:

```bash
pip install -r requirements-full.txt
```

- If you need to run the full local ingestion/tuning toolchain after pulling a fresh environment, install from `requirements-full.txt`.

to remove generated changes before committing.
