"""The error contract: a stable envelope + exit codes agents can branch on.

Every expected failure raises a `PscError` carrying a typed `ErrorType`. The
CLI catches it, prints the JSON envelope (to stderr, or stdout under
`--output json`), and exits with the code mapped here. The mapping is part of
the public contract — don't renumber without a major version bump.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorType(str, Enum):
    INPUT = "input"  # unreadable/invalid config or file
    VALIDATION = "validation"  # bad user input (invalid IP, bad object spec)
    NOT_FOUND = "not_found"  # object/IP not found (with --strict)
    CONFLICT = "conflict"  # unsafe/blocked plan; refused
    TRANSPORT = "transport"  # live API connection failure
    AUTH = "auth"  # live API auth failure
    CONFIG = "config"  # profile/config problem
    INTERNAL = "internal"  # unexpected bug


EXIT_CODES: dict[ErrorType, int] = {
    ErrorType.INTERNAL: 1,
    # 2 is reserved for Typer usage errors.
    ErrorType.INPUT: 3,
    ErrorType.VALIDATION: 4,
    ErrorType.NOT_FOUND: 5,
    ErrorType.CONFLICT: 6,
    ErrorType.TRANSPORT: 7,
    ErrorType.AUTH: 8,
    ErrorType.CONFIG: 9,
}


class PscError(Exception):
    """A typed, user-facing error. Carries enough to build the JSON envelope."""

    def __init__(
        self,
        message: str,
        error_type: ErrorType = ErrorType.INTERNAL,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.details = details or {}

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.error_type, 1)

    def envelope(self) -> dict[str, Any]:
        env: dict[str, Any] = {"error": self.message, "type": self.error_type.value}
        if self.details:
            env["details"] = self.details
        return env
