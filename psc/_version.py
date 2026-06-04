"""Single source of truth for the package version.

`release.yml` validates the git tag against this value before publishing.
Keep this in sync with `[project].version` in `pyproject.toml`.
"""

__version__ = "0.2.5"
