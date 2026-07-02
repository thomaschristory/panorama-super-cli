"""Framework-free config/profile CRUD (issue #83).

Pure `Config -> Config` transforms with no Textual dependency, so the mutation
logic is unit-testable in isolation. The TUI screen collects form fields, calls
one of these, and persists the result via `psc.config.loader.save_config`
(atomic 0600 write) — the writing is never reimplemented here.

Validation choices (documented, mirroring `psc profile`):
- add_or_update_profile: an existing name is an UPSERT (the profile is updated
  in place, order preserved), not an error — parity with `psc profile add`.
- remove_profile: removing a NONEXISTENT profile is a hard error (NOT_FOUND),
  so a misspelled name never silently no-ops. Removing the current default
  profile CLEARS default_profile (set to None).
- set_default_profile: pointing default at a NONEXISTENT profile is rejected
  (NOT_FOUND).
- set_default_output: only the known output formats are accepted (VALIDATION).
"""

from __future__ import annotations

from psc.config.models import Config, Profile
from psc.output.errors import ErrorType, PscError
from psc.output.format import OutputFormat

# The output formats a profile default may take, so the screen can offer a
# closed picker and the helper can reject anything else.
VALID_OUTPUTS: tuple[str, ...] = tuple(f.value for f in OutputFormat)


def add_or_update_profile(config: Config, profile: Profile) -> Config:
    """Add `profile`, or update an existing one of the same name in place.

    An existing name is an upsert (fields replaced, list position preserved);
    a new name is appended. Returns the mutated config for `save_config`.
    """
    for i, existing in enumerate(config.profiles):
        if existing.name == profile.name:
            config.profiles[i] = profile
            return config
    config.profiles.append(profile)
    return config


def remove_profile(config: Config, name: str) -> Config:
    """Remove the profile named `name`; clear default_profile if it matched.

    Removing a nonexistent profile raises (NOT_FOUND) rather than silently
    no-opping, so a typo is caught instead of masquerading as success.
    """
    if not any(p.name == name for p in config.profiles):
        raise PscError(f"no profile named '{name}'", ErrorType.NOT_FOUND)
    config.profiles = [p for p in config.profiles if p.name != name]
    if config.default_profile == name:
        config.default_profile = None
    return config


def set_default_profile(config: Config, name: str) -> Config:
    """Set the default profile to `name` (must be an existing profile)."""
    if not any(p.name == name for p in config.profiles):
        raise PscError(f"cannot set default to '{name}': no such profile", ErrorType.NOT_FOUND)
    config.default_profile = name
    return config


def set_default_output(config: Config, output: str) -> Config:
    """Set the default output format (must be a known OutputFormat value)."""
    if output not in VALID_OUTPUTS:
        raise PscError(
            f"invalid output '{output}' (one of: {', '.join(VALID_OUTPUTS)})",
            ErrorType.VALIDATION,
        )
    config.defaults.output = output
    return config
