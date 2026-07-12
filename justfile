default:
    @just --list

# Install / refresh deps
sync:
    uv sync

# Lint: ruff + mypy --strict
lint:
    uv run ruff check psc tests
    uv run ruff format --check psc tests
    uv run mypy --strict psc

# Auto-fix lint issues
fix:
    uv run ruff check --fix psc tests
    uv run ruff format psc tests

# Run all tests
test *args:
    uv run pytest {{args}}

# Run tests with coverage
test-cov:
    uv run pytest --cov=psc --cov-report=term-missing

# Install pre-commit hooks
hooks:
    uv run pre-commit install

# Run all pre-commit hooks against all files
hooks-all:
    uv run pre-commit run --all-files

# Smoke-run the CLI
psc *args:
    uv run psc {{args}}

# Check AGENTS.md is in sync with CLAUDE.md
sync-agents:
    uv run python scripts/sync_agents_md.py

# Serve docs locally
docs:
    uv run zensical serve

# Build docs (strict — fails on broken links / missing anchors)
docs-build:
    uv run zensical build --strict
