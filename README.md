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

## Repository hygiene

- `brain_index/` stores local Chroma persistent vector data and should be treated as generated environment state.
- Do not commit database artifacts (`brain_index/data_level0.bin`, `brain_index/chroma.sqlite3`) to git.
- If these files are modified by ingestion or local testing, run:

```bash
git restore -- brain_index
```

## Dependency profile for deployment vs local use

- Vercel API functions use a minimal dependency set in `requirements.txt` to stay within Lambda install limits.
- Local/CLI development using full model tooling can use:

```bash
pip install -r requirements-full.txt
```

- If you need to run the full local ingestion/tuning toolchain after pulling a fresh environment, install from `requirements-full.txt`.

to remove generated changes before committing.
