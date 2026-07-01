"""Config schema: profiles for live access + tool defaults.

Kept deliberately small. The offline path (`--config file.xml`) needs no config
at all; this exists so live profiles and an opt-in naming scheme have a home.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from psc.core.naming import NamingScheme

if TYPE_CHECKING:
    from psc.core.source import LiveSource

_API_KEY_ENV = "PSC_API_KEY"


class Profile(BaseModel):
    """A live Panorama target. The API key is stored here for v0.1; treat the
    config file as a secret (it is created `0600`).

    The key may instead be supplied via the `PSC_API_KEY` environment variable
    to keep it off disk entirely; see `resolved_api_key`.
    """

    name: str
    hostname: str
    api_key: str = ""
    port: int = 443
    verify_ssl: bool = True
    device_group: str | None = None  # default scope for this profile

    def resolved_api_key(self) -> str:
        """The effective API key, precedence env > profile file.

        A non-empty `PSC_API_KEY` overrides the stored `api_key`, letting users
        keep the secret out of the config file. An unset/empty var falls back to
        the on-disk value.
        """
        return os.environ.get(_API_KEY_ENV) or self.api_key

    def to_live_source(self) -> LiveSource:
        """Build a `LiveSource` for this profile, honouring the env-key override."""
        from psc.core.source import LiveSource  # noqa: PLC0415 — avoid SDK import at config load

        return LiveSource(
            self.hostname,
            self.resolved_api_key(),
            port=self.port,
            verify=self.verify_ssl,
        )


class Defaults(BaseModel):
    output: str = "table"
    naming: NamingScheme = Field(default_factory=NamingScheme)


class Config(BaseModel):
    default_profile: str | None = None
    profiles: list[Profile] = Field(default_factory=list)
    defaults: Defaults = Field(default_factory=Defaults)

    def profile(self, name: str | None) -> Profile | None:
        target = name or self.default_profile
        if target is None:
            return None
        return next((p for p in self.profiles if p.name == target), None)
