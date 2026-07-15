# Install

`psc` is published on PyPI as **`panorama-super-cli`** and ships the `psc`
command.

=== "uv (recommended)"

    ```console
    uv tool install panorama-super-cli
    ```

=== "pipx"

    ```console
    pipx install panorama-super-cli
    ```

=== "pip"

    ```console
    pip install panorama-super-cli
    ```

Verify:

```console
$ psc version
psc 0.1.0
```

(`psc --version` works too. `psc version check` reports whether a newer release
is available on PyPI.)

`psc` requires Python 3.12+. It has no external service dependencies for the
offline path; the live path talks to Panorama via
[`pan-os-python`](https://github.com/PaloAltoNetworks/pan-os-python), which is
installed automatically.

## Install the agent Skill (optional)

`psc` bundles an [Agent Skill](../guides/using-with-ai-agents.md#bundled-skill)
so an AI coding agent can drive it correctly from a cold start. Drop it where
your harness loads user-scoped skills (dry-run by default — add `--apply` to
write):

```console
psc skill install --target claude-code --apply   # or: codex | gemini | copilot
```

See [Using with AI agents](../guides/using-with-ai-agents.md) for details.

## Get a config to point it at

The fastest way to try `psc` is offline against an exported config:

- **GUI:** Device → Setup → Operations → Export named configuration snapshot.
- **CLI:** `scp export configuration from <panorama> ...`, or
  `show config running | save` and copy it off the box.

Then:

```console
psc --config panorama.xml find ip 10.0.0.10
```

See [First run](first-run.md) next.
