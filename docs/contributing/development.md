# Development

## Setup

```console
git clone https://github.com/thomaschristory/panorama-super-cli
cd panorama-super-cli
just sync          # uv sync — installs runtime + dev deps
just hooks         # install pre-commit hooks
```

Python 3.12+ is required; `uv` manages the interpreter.

## Day-to-day

```console
just test          # pytest
just lint          # ruff check + ruff format --check + mypy --strict
just fix           # auto-fix ruff issues + format
just psc <args>    # run the local CLI, e.g. just psc -c tests/fixtures/panorama-config.xml find ip 10.0.0.10
just docs          # serve the docs locally
```

## Architecture

The hard split is **backend (`psc/core`) vs frontend (`psc/cli`)** — see the
[contributor cheat sheet](https://github.com/thomaschristory/panorama-super-cli/blob/main/CLAUDE.md).
`psc/core` imports nothing from `psc/cli` or any UI framework; engines return
Pydantic models, the CLI formats them. A future web UI would import `psc.core`
directly.

Features are independent: each `psc/cli/*_cmds.py` maps to one `core` engine and
can be removed without touching the others.

## Conventions

- Full type annotations; `mypy --strict` must pass.
- Pydantic v2 for structured data.
- Conventional Commits.
- **TDD for safety-critical paths.** Merge repointing, blockers, the apply
  round-trip, and the shadow-rename refusal must have tests. Write the failing
  test first.
- Comments explain non-obvious *why*, never *what*.

## Testing

```console
just test-cov      # coverage report
```

The test suite runs entirely offline against `tests/fixtures/panorama-config.xml`
and via subprocess for the exit-code contract. No Panorama is required.

## AGENTS.md

`AGENTS.md` is generated from `CLAUDE.md`:

```console
just sync-agents   # regenerate; CI fails if it drifts
```
