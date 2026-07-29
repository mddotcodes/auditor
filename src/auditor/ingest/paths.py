"""Normalize and validate relative source paths for safe materialization.

All user-supplied paths must resolve under the job project directory. We reject
``..`` segments, absolute paths, Windows drive letters, NUL bytes, empty names,
and other traversal tricks before any filesystem write.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from auditor.ingest.errors import PathTraversalError

# Windows drive / UNC-ish prefixes (even when using posix separators).
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

# Foundry layout roots we preserve when present in the payload.
FOUNDRY_ROOT_DIRS: frozenset[str] = frozenset({"src", "test", "lib", "script"})
FOUNDRY_CONFIG_NAME: str = "foundry.toml"


def normalize_source_path(raw: str) -> str:
    """Normalize a user path to a relative posix path or raise.

    Rules
    -----
    - Reject empty / whitespace-only paths
    - Reject NUL bytes
    - Reject absolute paths (posix and Windows)
    - Reject ``..`` and empty (``.``-only) segments that escape
    - Collapse redundant ``.`` and duplicate slashes via :class:`PurePosixPath`
    - Convert backslashes to forward slashes before parsing
    - Reject Windows reserved device names as any path segment
    """
    if "\x00" in raw:
        msg = f"source path contains NUL byte: {raw!r}"
        raise PathTraversalError(msg)

    stripped = raw.strip()
    if not stripped:
        msg = "source path must not be empty"
        raise PathTraversalError(msg)

    # Normalize separators early so Windows-style paths are checked uniformly.
    candidate = stripped.replace("\\", "/")

    if candidate.startswith("/") or candidate.startswith("~"):
        msg = f"absolute source path is not allowed: {raw!r}"
        raise PathTraversalError(msg)

    if _WINDOWS_DRIVE.match(candidate):
        msg = f"Windows drive path is not allowed: {raw!r}"
        raise PathTraversalError(msg)

    if candidate.startswith("//") or candidate.startswith("\\\\"):
        msg = f"UNC / network path is not allowed: {raw!r}"
        raise PathTraversalError(msg)

    pure = PurePosixPath(candidate)
    parts = pure.parts

    if not parts:
        msg = f"source path resolved empty: {raw!r}"
        raise PathTraversalError(msg)

    if pure.is_absolute():
        msg = f"absolute source path is not allowed: {raw!r}"
        raise PathTraversalError(msg)

    cleaned: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            msg = f"path traversal segment '..' is not allowed: {raw!r}"
            raise PathTraversalError(msg)
        # Reject segment-level tricks (hidden absolute-ish, NUL already checked).
        if "\x00" in part:
            msg = f"source path segment contains NUL: {raw!r}"
            raise PathTraversalError(msg)
        # Windows reserved device names (case-insensitive, optional extension).
        stem = part.split(".", 1)[0].upper()
        if stem in _RESERVED_NAMES:
            msg = f"reserved device name is not allowed in path: {raw!r}"
            raise PathTraversalError(msg)
        cleaned.append(part)

    if not cleaned:
        msg = f"source path resolved empty after normalization: {raw!r}"
        raise PathTraversalError(msg)

    return "/".join(cleaned)


def looks_like_foundry_layout(paths: list[str]) -> bool:
    """True if any path already uses Foundry roots or is ``foundry.toml``."""
    for path in paths:
        if path == FOUNDRY_CONFIG_NAME:
            return True
        first = path.split("/", 1)[0]
        if first in FOUNDRY_ROOT_DIRS:
            return True
    return False


def is_flat_solidity_set(paths: list[str]) -> bool:
    """True when all ``.sol`` files are basename-only (no directory prefix).

    Non-``.sol`` files (e.g. ``foundry.toml``) are ignored for the flat check.
    """
    sol_paths = [p for p in paths if p.endswith(".sol")]
    if not sol_paths:
        return False
    return all("/" not in p for p in sol_paths)


def map_source_paths(sources: dict[str, str]) -> dict[str, str]:
    """Map logical source keys to on-disk relative paths under the project.

    - Preserve paths when the payload already looks like a Foundry layout.
    - If every ``.sol`` file is a flat basename (no ``src/`` prefix), place them
      under ``src/``.
    - Otherwise keep paths as given (after normalization).
    """
    normalized: dict[str, str] = {}
    for raw_path, content in sources.items():
        key = normalize_source_path(raw_path)
        if key in normalized:
            msg = f"duplicate source path after normalization: {key!r}"
            raise PathTraversalError(msg)
        normalized[key] = content

    keys = list(normalized.keys())
    if looks_like_foundry_layout(keys):
        return normalized

    if is_flat_solidity_set(keys):
        mapped: dict[str, str] = {}
        for path, content in normalized.items():
            if path.endswith(".sol"):
                mapped[f"src/{path}"] = content
            else:
                mapped[path] = content
        return mapped

    return normalized


def safe_project_join(project_dir: Path, relative: str) -> Path:
    """Join ``relative`` under ``project_dir`` and ensure it does not escape.

    Uses resolved physical paths so symlink tricks cannot point outside the
    project root. The parent of the target must stay inside the project after
    resolution.
    """
    rel = normalize_source_path(relative)
    project_root = project_dir.resolve()
    target = (project_root / rel).resolve()

    try:
        target.relative_to(project_root)
    except ValueError as exc:
        msg = f"path escapes project directory: {relative!r}"
        raise PathTraversalError(msg) from exc

    # Ensure every intermediate parent also stays under project (symlink parents).
    try:
        # If the file does not exist yet, resolve parents.
        parent = target.parent
        if parent != project_root:
            parent.resolve().relative_to(project_root)
    except ValueError as exc:
        msg = f"path parent escapes project directory: {relative!r}"
        raise PathTraversalError(msg) from exc

    return target


def write_text_under_project(project_dir: Path, relative: str, content: str) -> Path:
    """Write UTF-8 text to ``project_dir / relative`` with traversal checks.

    Creates parent directories as needed. Refuses to overwrite a path that is
    already a symlink (symlink swap / escape hardening).
    """
    target = safe_project_join(project_dir, relative)

    # Create parents carefully without following a symlink-as-directory escape.
    project_root = project_dir.resolve()
    rel_parts = PurePosixPath(normalize_source_path(relative)).parts
    current = project_root
    for part in rel_parts[:-1]:
        current = current / part
        if current.is_symlink():
            msg = f"refusing to traverse symlink in project path: {relative!r}"
            raise PathTraversalError(msg)
        if not current.exists():
            current.mkdir(parents=False, exist_ok=False)
        elif not current.is_dir():
            msg = f"parent path is not a directory: {relative!r}"
            raise PathTraversalError(msg)
        # Re-check after create/exists that we are still under root.
        try:
            current.resolve().relative_to(project_root)
        except ValueError as exc:
            msg = f"path parent escapes project directory: {relative!r}"
            raise PathTraversalError(msg) from exc

    if target.exists() and target.is_symlink():
        msg = f"refusing to write through symlink: {relative!r}"
        raise PathTraversalError(msg)

    target.write_text(content, encoding="utf-8", newline="\n")
    return target
