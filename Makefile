# darkroom_ai - GNU make targets.
# Every recipe is ONE command with no shell syntax, so it behaves the same from cmd, PowerShell and Git Bash.
# Text after "##" is what `make help` prints.

.DEFAULT_GOAL := help

.PHONY: help install install-ml update lock run doctor test test-integration cov lint format format-check \
        ruff-fix fix typecheck quality audit offline-check build clean clean-all

help: ## List the available targets
	@poetry run python scripts/make_help.py

install: ## Install core + dev dependencies into .venv (removes the ml extra if it was installed)
	poetry install --with dev

install-ml: ## Install core + dev + the ml extra (onnxruntime-gpu, rembg; large download)
	poetry install --with dev --extras ml

update: ## Update dependencies within the pins in pyproject.toml and rewrite poetry.lock
	poetry update

lock: ## Re-resolve and rewrite poetry.lock
	poetry lock

run: ## Run the CLI; pass arguments with ARGS="..."
	poetry run darkroom $(ARGS)

doctor: ## Check the local setup (config, apps, model server)
	poetry run darkroom doctor

test: ## Run the unit tests (integration tests skip themselves)
	poetry run pytest

test-integration: ## Run integration tests; set DARKROOM_IT_LIGHTROOM / _PHOTOSHOP / _LLM / _GPU =1 first
	poetry run pytest -m integration

cov: ## Run tests with coverage (terminal report + htmlcov/)
	poetry run pytest --cov --cov-report=term-missing --cov-report=html

lint: ## Lint with ruff
	poetry run ruff check .

format: ## Format the code with ruff
	poetry run ruff format .

format-check: ## Check formatting without changing files
	poetry run ruff format --check .

ruff-fix: ## Apply ruff's automatic lint fixes
	poetry run ruff check --fix .

fix: ruff-fix format ## ruff --fix, then format

typecheck: ## Type-check with mypy
	poetry run mypy

quality: lint format-check typecheck test ## lint + format-check + typecheck + test (end-of-phase check)

audit: ## Check installed packages for known vulnerabilities (pip-audit; uses the network)
	poetry run pip-audit

offline-check: ## Run the offline-guard tests (no non-loopback traffic, no network imports)
	poetry run pytest tests/unit/test_offline_guard.py

build: ## Build the sdist and wheel into dist/
	poetry build

clean: ## Remove caches and build output (never .venv, catalogs, photos or user data)
	poetry run python scripts/clean.py

clean-all: ## clean + dist/ and htmlcov/
	poetry run python scripts/clean.py --all
