# Auditor — developer task runner (see docs/decisions/0001-orchestration-language.md)
.PHONY: help install fmt lint test test-report static-corpus build-image release-dry-run clean

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
IMAGE_NAME ?= auditor
IMAGE_TAG ?= local

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install package + dev tooling (editable)
	$(PIP) install -e ".[dev]"

fmt: ## Format code with Ruff
	$(PYTHON) -m ruff format src tests
	$(PYTHON) -m ruff check --fix src tests

lint: ## Ruff lint + format check + mypy
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests
	$(PYTHON) -m mypy

test: ## Run pytest
	$(PYTHON) -m pytest

test-report: ## Run pytest and write docs/test-reports/latest.md
	./scripts/gen-test-report.sh

static-corpus: ## Free static corpus batch (Docker --profile static --no-llm; no API keys)
	./scripts/batch-static-corpus.sh

build-image: ## Build local Docker image (docker/Dockerfile → auditor:local)
	@if [ ! -f docker/Dockerfile ] && [ ! -f Dockerfile ]; then \
		echo "No Dockerfile yet (Phase 1). Skipping image build."; \
	else \
		DOCKERFILE=docker/Dockerfile; \
		if [ ! -f "$$DOCKERFILE" ]; then DOCKERFILE=Dockerfile; fi; \
		docker build -f "$$DOCKERFILE" -t $(IMAGE_NAME):$(IMAGE_TAG) .; \
	fi

release-dry-run: ## Build release image locally only (no tag push, no GHCR)
	@if [ ! -f docker/Dockerfile ]; then \
		echo "Missing docker/Dockerfile"; exit 1; \
	fi
	docker build -f docker/Dockerfile \
		-t $(IMAGE_NAME):$(IMAGE_TAG) \
		-t $(IMAGE_NAME):release-dry-run \
		.
	@echo "Built $(IMAGE_NAME):$(IMAGE_TAG) and $(IMAGE_NAME):release-dry-run (local only; not pushed)"

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
