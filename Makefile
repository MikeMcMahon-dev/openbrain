PYTHON_SRCS := scripts brain_server api
LOG_DIR := .logs
NOW := $(shell date +%Y%m%d-%H%M%S)

.PHONY: lint lint-py lint-md lint-py-fix smoke smoke-local smoke-live check
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

smoke-live:
	@if [ -z "$(SMOKE_URL)" ]; then \
		echo "SMOKE_URL is required. Example: make smoke-live SMOKE_URL=https://your-project.vercel.app"; \
		exit 1; \
	fi
	@python scripts/smoke_checks.py --live "$(SMOKE_URL)"

check: lint smoke

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
