"""Materialize untrusted Solidity sources into a job Foundry project."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auditor.ingest.errors import MaterializeError, PayloadTooLargeError
from auditor.ingest.foundry import ensure_foundry_dirs, write_foundry_toml
from auditor.ingest.limits import IngestLimits, check_payload_limits
from auditor.ingest.paths import map_source_paths, write_text_under_project
from auditor.ingest.pragma import PragmaInfo, analyze_pragmas

if TYPE_CHECKING:
    from auditor.contracts.layout import JobPaths

# Optional M3.2 hook: vendored forge-std / OZ into project/lib/.
_apply_default_vendor_libs: Callable[..., Any] | None
try:
    from auditor.deps import apply_default_vendor_libs as _apply_default_vendor_libs
except ImportError:  # pragma: no cover - deps package always present in-tree
    _apply_default_vendor_libs = None


@dataclass(frozen=True, slots=True)
class MaterializeResult:
    """Outcome of writing sources into a job project directory."""

    project_dir: Path
    files_written: tuple[str, ...]
    pragma_info: PragmaInfo
    total_bytes: int
    total_lines: int
    foundry_toml: str
    """Relative path of the written ``foundry.toml`` (always under project)."""


def materialize_sources(
    job_paths: JobPaths,
    sources: Mapping[str, str],
    *,
    apply_vendor_libs: bool = True,
    limits: IngestLimits | None = None,
) -> MaterializeResult:
    """Validate and write ``sources`` under ``job_paths.project``.

    Parameters
    ----------
    job_paths:
        Job layout helper; only ``project`` is written by this function.
    sources:
        Map of relative path → file text (Solidity and optional ``foundry.toml``).
    apply_vendor_libs:
        When true and :mod:`auditor.deps` is installed, call
        ``apply_default_vendor_libs(project_dir)`` after writing config.
    limits:
        Override size caps; defaults to :meth:`IngestLimits.from_env`.

    Returns
    -------
    MaterializeResult
        Paths written, pragma reconciliation, and size stats.

    Raises
    ------
    PathTraversalError
        Unsafe or escaping source paths.
    PayloadTooLargeError
        File count / byte / LOC limits exceeded.
    PragmaConflictError
        Incompatible ``pragma solidity`` ranges.
    InvalidFoundryConfigError
        User-supplied ``foundry.toml`` failed safety validation.
    MaterializeError
        Other materialization failures.
    """
    if not sources:
        msg = "sources must not be empty"
        raise PayloadTooLargeError(msg)

    # Copy to a plain dict[str, str] for stable processing.
    raw = dict(sources)

    resolved_limits = limits if limits is not None else IngestLimits.from_env()
    total_bytes, total_lines = check_payload_limits(raw, resolved_limits)

    # Normalize / remap paths before pragma scan so file lists match on-disk layout.
    mapped = map_source_paths(raw)
    pragma_info = analyze_pragmas(mapped)

    project_dir = job_paths.project
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"cannot create project directory {project_dir}: {exc}"
        raise MaterializeError(msg) from exc

    ensure_foundry_dirs(project_dir)

    user_toml = mapped.pop("foundry.toml", None)
    written: list[str] = []

    for rel_path, content in sorted(mapped.items()):
        write_text_under_project(project_dir, rel_path, content)
        written.append(rel_path)

    foundry_rel = write_foundry_toml(
        project_dir,
        solc_version=pragma_info.solc_version,
        user_toml=user_toml,
    )
    written.append(foundry_rel)

    if apply_vendor_libs and _apply_default_vendor_libs is not None:
        _apply_default_vendor_libs(project_dir)

    # Stable order for callers / tests.
    written_sorted = tuple(sorted(set(written)))
    return MaterializeResult(
        project_dir=project_dir,
        files_written=written_sorted,
        pragma_info=pragma_info,
        total_bytes=total_bytes,
        total_lines=total_lines,
        foundry_toml=foundry_rel,
    )
