<!-- Conventional Commit title, e.g. feat(dedup): merge service objects -->

## What & why

<!-- What does this change and why? Link the issue: Closes #N -->

## Safety

<!-- For any change to writes/merge/rename/apply: -->
- [ ] Dry-run remains the default; writes still require `--apply`.
- [ ] References are repointed before deletes/renames.
- [ ] Unsafe cases add a `blocker` rather than doing something surprising.

## Checklist

- [ ] `just test` green
- [ ] `just lint` clean (ruff + mypy --strict)
- [ ] Tests added for safety-critical paths
- [ ] Docs / CHANGELOG updated if user-facing
- [ ] `just sync-agents` run if `CLAUDE.md` changed
