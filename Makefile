PYTHON_SRCS := scripts brain_server

.PHONY: lint lint-py lint-md

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

lint-md:
	python scripts/lint_markdown.py
