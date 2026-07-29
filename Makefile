# Auditor — developer task runner (see docs/decisions/0001-orchestration-language.md)
.PHONY: help install fmt lint test build-image clean

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

build-image: ## Build local Docker image (docker/Dockerfile → auditor:local)
	@if [ ! -f docker/Dockerfile ] && [ ! -f Dockerfile ]; then \
		echo "No Dockerfile yet (Phase 1). Skipping image build."; \
	else \
		DOCKERFILE=docker/Dockerfile; \
		if [ ! -f "$$DOCKERFILE" ]; then DOCKERFILE=Dockerfile; fi; \
		docker build -f "$$DOCKERFILE" -t $(IMAGE_NAME):$(IMAGE_TAG) .; \
	fi

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
