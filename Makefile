PYTHON_SRCS := scripts brain_server api
LOG_DIR := .logs
NOW := $(shell date +%Y%m%d-%H%M%S)

.PHONY: lint lint-py lint-md lint-py-fix lint-ci smoke smoke-local smoke-live smoke-preview smoke-ci ci check
.PHONY: check-log smoke-log smoke-live-log
.PHONY: pdf-fixtures pdf-unit pdf-eval pdf-eval-live
.PHONY: docx-fixtures docx-unit url-unit docx-url-eval docx-url-eval-live
.PHONY: migrate migrate-execute migrate-test
.PHONY: install-hooks dev-install

migrate:
	@.venv/bin/python3 scripts/migrate_thoughts.py

migrate-execute:
	@.venv/bin/python3 scripts/migrate_thoughts.py --execute

migrate-test:
	@.venv/bin/python3 scripts/test_migration.py

install-hooks:
	@git config core.hooksPath scripts
	@chmod +x scripts/pre-commit
	@echo "Pre-commit hook installed. Blocks: direct commits to main, OpenBrain tokens in staged files."

dev-install:
	@python3 -m pip install -r requirements-dev.txt -q
	@$(MAKE) install-hooks
	@echo "Dev dependencies installed and hooks wired."

lint: lint-py lint-md
	@echo "Running lint checks..."

lint-py:
	@if ! command -v ruff >/dev/null 2>&1; then \
		echo "Missing required dev dependency: ruff"; \
		echo "Install with: pip install -r requirements-dev.txt"; \
		exit 1; \
	fi
	python -m compileall $(PYTHON_SRCS)
	ruff check $(PYTHON_SRCS)
	ruff format --check $(PYTHON_SRCS)

lint-py-fix:
	@echo "Auto-fixing Python style issues with Ruff..."
	ruff check --fix $(PYTHON_SRCS)
	ruff format $(PYTHON_SRCS)

lint-md:
	python scripts/lint_markdown.py

smoke: smoke-local

smoke-local:
	@python scripts/smoke_checks.py

# What CI runs. --read-only skips the PDF and DOCX/URL groups, the only cases that POST to
# /api/ingest — there is one live vault and no staging DB, so an unattended run must not write.
smoke-ci:
	@python scripts/smoke_checks.py --read-only

# Exactly what .github/workflows/ci.yml lints. Deliberately narrower than lint-py, which also
# covers scripts/ (~100 pre-existing E501s, the "Lint pass 2" backlog) and runs a format check.
# Keep this in step with the workflow — if they drift, `make ci` stops predicting CI.
lint-ci:
	@ruff check api/ mcp_server/ tests/

# Reproduce the full CI gate locally, in the same order.
ci: lint-ci test smoke-ci

# Tier 2: smoke a protected Vercel PREVIEW build before merging. Needs
# VERCEL_AUTOMATION_BYPASS_SECRET in .env.local (the project runs SSO protection on previews).
#   make smoke-preview SMOKE_URL=https://openbrain-<hash>-....vercel.app
smoke-preview: smoke-live

smoke-live:
	@if [ -z "$(SMOKE_URL)" ]; then \
		echo "SMOKE_URL is required. Example: make smoke-live SMOKE_URL=https://your-project.vercel.app"; \
		exit 1; \
	fi
	@python scripts/smoke_checks.py --live "$(SMOKE_URL)"

test:
	@# Must run from tests/: the repo root holds a `vault/` symlink into an iCloud
	@# store that is unreadable for some users, so pytest's rootdir scandir crashes
	@# if it starts at the root. tests/conftest.py puts the repo on sys.path.
	@cd tests && python -m pytest -q

test-supersession:
	@# Supersession harness (ADR-018). Deterministic — no DB, no live embeddings.
	@# Suites A/B/E arrive with their phases; Suite C (retrieval) is here today.
	@cd tests && python -m pytest test_supersession_harness.py -v -rx

test-supersession-regression:
	@# The C3/C4 regression pair only — stale content stays buried, evergreen doesn't
	@# get buried with it. Run on every change (see ADR-018 validation).
	@cd tests && python -m pytest test_supersession_harness.py -q -rx -k "C3 or C4"

capability-audit:
	@# Deployment-completeness gate (ADR-018): fail on any capability that landed with
	@# no caller and isn't registered in scripts/capability_audit.allow.json. exit 1 = block.
	@python scripts/capability_audit.py

check: lint smoke capability-audit

check-log:
	@mkdir -p "$(LOG_DIR)"
	@$(MAKE) check 2>&1 | tee "$(LOG_DIR)/check-$(NOW).log"

smoke-log:
	@mkdir -p "$(LOG_DIR)"
	@$(MAKE) smoke 2>&1 | tee "$(LOG_DIR)/smoke-$(NOW).log"

smoke-live-log:
	@if [ -z "$(SMOKE_URL)" ]; then \
		echo "SMOKE_URL is required. Example: make smoke-live-log SMOKE_URL=https://your-project.vercel.app"; \
		exit 1; \
	fi
	@mkdir -p "$(LOG_DIR)"
	@$(MAKE) smoke-live SMOKE_URL="$(SMOKE_URL)" 2>&1 | tee "$(LOG_DIR)/smoke-live-$(NOW).log"

# PDF test targets
pdf-fixtures:
	@python scripts/test_fixtures/generate_pdf_fixtures.py

pdf-unit:
	@python scripts/test_pdf_extraction.py

pdf-eval:
	@python scripts/test_pdf_ingest_eval.py

pdf-eval-live:
	@OPENBRAIN_API_BASE=https://openbrain-rouge.vercel.app python scripts/test_pdf_ingest_eval.py

# DOCX + URL test targets
docx-fixtures:
	@python scripts/test_fixtures/generate_docx_fixtures.py

docx-unit:
	@python scripts/test_docx_extraction.py

url-unit:
	@python scripts/test_url_fetch.py

docx-url-eval:
	@python scripts/test_docx_url_ingest_eval.py

docx-url-eval-live:
	@OPENBRAIN_API_BASE=https://openbrain-rouge.vercel.app python scripts/test_docx_url_ingest_eval.py
