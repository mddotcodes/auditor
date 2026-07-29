"""Size and count limits for ingested Solidity payloads.

Defaults are conservative for untrusted multi-file projects and can be
overridden via environment variables (see ``docs/ingest.md``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from auditor.ingest.errors import PayloadTooLargeError

DEFAULT_MAX_SOURCE_BYTES: Final[int] = 2_000_000
DEFAULT_MAX_SOURCE_FILES: Final[int] = 200
DEFAULT_MAX_FILE_BYTES: Final[int] = 512_000
DEFAULT_MAX_LOC: Final[int] = 50_000

_ENV_MAX_SOURCE_BYTES: Final[str] = "AUDIT_MAX_SOURCE_BYTES"
_ENV_MAX_SOURCE_FILES: Final[str] = "AUDIT_MAX_SOURCE_FILES"
_ENV_MAX_FILE_BYTES: Final[str] = "AUDIT_MAX_FILE_BYTES"
_ENV_MAX_LOC: Final[str] = "AUDIT_MAX_SOURCE_LOC"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"Environment variable {name}={raw!r} is not a valid integer"
        raise ValueError(msg) from exc
    if value <= 0:
        msg = f"Environment variable {name} must be a positive integer (got {value})"
        raise ValueError(msg)
    return value


@dataclass(frozen=True, slots=True)
class IngestLimits:
    """Hard caps applied before writing any source bytes to disk."""

    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    max_source_files: int = DEFAULT_MAX_SOURCE_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_loc: int = DEFAULT_MAX_LOC

    @classmethod
    def from_env(cls) -> IngestLimits:
        """Load limits from process environment (container / local)."""
        return cls(
            max_source_bytes=_env_int(_ENV_MAX_SOURCE_BYTES, DEFAULT_MAX_SOURCE_BYTES),
            max_source_files=_env_int(_ENV_MAX_SOURCE_FILES, DEFAULT_MAX_SOURCE_FILES),
            max_file_bytes=_env_int(_ENV_MAX_FILE_BYTES, DEFAULT_MAX_FILE_BYTES),
            max_loc=_env_int(_ENV_MAX_LOC, DEFAULT_MAX_LOC),
        )


def count_lines(content: str) -> int:
    """Estimate LOC: number of newline-separated lines (empty content → 0)."""
    if not content:
        return 0
    # Count lines without allocating a large list for huge payloads.
    return content.count("\n") + (0 if content.endswith("\n") else 1)


def utf8_size(content: str) -> int:
    """Return UTF-8 byte length of ``content``."""
    return len(content.encode("utf-8"))


def check_payload_limits(
    sources: dict[str, str],
    limits: IngestLimits,
) -> tuple[int, int]:
    """Validate file count, per-file size, total bytes, and LOC.

    Returns
    -------
    total_bytes, total_lines
    """
    n_files = len(sources)
    if n_files == 0:
        msg = "sources must not be empty"
        raise PayloadTooLargeError(msg)
    if n_files > limits.max_source_files:
        msg = (
            f"too many source files: {n_files} > max {limits.max_source_files} "
            f"(set {_ENV_MAX_SOURCE_FILES} to raise)"
        )
        raise PayloadTooLargeError(msg)

    total_bytes = 0
    total_lines = 0
    for path, content in sources.items():
        size = utf8_size(content)
        if size > limits.max_file_bytes:
            msg = (
                f"file {path!r} is {size} bytes > max per-file "
                f"{limits.max_file_bytes} (set {_ENV_MAX_FILE_BYTES} to raise)"
            )
            raise PayloadTooLargeError(msg)
        total_bytes += size
        total_lines += count_lines(content)

    if total_bytes > limits.max_source_bytes:
        msg = (
            f"total source payload is {total_bytes} bytes > max "
            f"{limits.max_source_bytes} (set {_ENV_MAX_SOURCE_BYTES} to raise)"
        )
        raise PayloadTooLargeError(msg)

    if total_lines > limits.max_loc:
        msg = f"estimated LOC {total_lines} > max {limits.max_loc} (set {_ENV_MAX_LOC} to raise)"
        raise PayloadTooLargeError(msg)

    return total_bytes, total_lines
