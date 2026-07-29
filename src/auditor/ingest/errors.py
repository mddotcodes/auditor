"""Typed errors raised while materializing untrusted Solidity sources."""

from __future__ import annotations


class MaterializeError(Exception):
    """Base error for source ingestion / Foundry materialization failures."""


class PathTraversalError(MaterializeError, ValueError):
    """Raised when a source path escapes the job project directory."""


class PayloadTooLargeError(MaterializeError, ValueError):
    """Raised when file count, total bytes, per-file size, or LOC exceeds limits.

    Also used by gist fetch when remote content exceeds size caps.
    """

    def __init__(
        self,
        message: str,
        *,
        limit: int | None = None,
        actual: int | None = None,
    ) -> None:
        self.limit = limit
        self.actual = actual
        super().__init__(message)


class PragmaConflictError(MaterializeError, ValueError):
    """Raised when Solidity version pragmas cannot be satisfied together."""


class InvalidFoundryConfigError(MaterializeError, ValueError):
    """Raised when a user-supplied ``foundry.toml`` fails safety checks."""
