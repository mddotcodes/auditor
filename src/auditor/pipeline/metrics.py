"""Lightweight pre-flight metrics (M4.5) — no full audit required.

Pure stdlib helpers for SaaS / CLI size and complexity estimates:

- **LOC** — non-empty lines that are not ``//`` comments (block comments
  are not stripped; this is intentional for speed).
- **Cyclomatic proxy** — keyword count of ``if`` / ``else`` / ``for`` /
  ``while`` / ``require`` / ``unchecked`` / ``assembly`` (not true McCabe).
- **Token estimate** — ``ceil(total_chars / 4)`` over analyzed source text.
  No ``tiktoken`` (or other tokenizer) dependency; document alternatives
  (e.g. cl100k via tiktoken) if a host wants tighter LLM billing estimates.
- **Tool probe** — ``shutil.which`` for forge / slither / aderyn / echidna /
  mythril.

These functions do not start a FastAPI server; use
:func:`run_metrics_from_request` from a future ``POST /metrics`` handler.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from auditor.ingest.errors import PragmaConflictError
from auditor.ingest.pragma import analyze_pragmas

# Analyzer / compiler CLIs expected in the Docker image (optional locally).
_TOOL_NAMES: Final[tuple[str, ...]] = (
    "forge",
    "slither",
    "aderyn",
    "echidna",
    "mythril",
)

# Rough control-flow / complexity keywords (Solidity-oriented).
_CYCLOMATIC_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:if|else|for|while|require|unchecked|assembly)\b"
)

# Characters per estimated token (OpenAI-style char/4 heuristic).
_CHARS_PER_TOKEN: Final[int] = 4


@dataclass(frozen=True, slots=True)
class MetricsResult:
    """Aggregate pre-flight metrics for a source set or project tree."""

    loc_total: int
    """LOC across all analyzed ``.sol`` files (including under ``lib/``)."""

    loc_src_only: int
    """LOC excluding paths under top-level ``lib/`` (deps / vendored)."""

    file_count: int
    """Number of analyzed ``.sol`` files (including ``lib/``)."""

    approx_cyclomatic: int
    """Sum of cyclomatic-proxy keyword hits across all analyzed sources."""

    approx_tokens: int
    """``ceil(total_source_chars / 4)``; see module docstring."""

    tools_available: dict[str, bool]
    """``{tool_name: on_PATH}`` for forge, slither, aderyn, echidna, mythril."""

    pragma_hint: str | None
    """Best-effort Solidity version hint (pin, range, raw pragma, or conflict)."""


def probe_tools() -> dict[str, bool]:
    """Return whether known analyzer/compiler tools are on ``PATH``."""
    return {name: shutil.which(name) is not None for name in _TOOL_NAMES}


def count_loc(content: str) -> int:
    """Count non-empty lines that are not ``//`` (or ``///``) comments.

    A line whose first non-whitespace characters are ``//`` is excluded.
    Inline code followed by ``// comment`` still counts as one LOC.
    """
    n = 0
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("//"):
            continue
        n += 1
    return n


def approx_cyclomatic(content: str) -> int:
    """Heuristic complexity: count control-flow / require / assembly keywords."""
    return len(_CYCLOMATIC_RE.findall(content))


def approx_tokens(text: str) -> int:
    """Estimate LLM tokens as ``ceil(len(text) / 4)`` (no external tokenizer)."""
    if not text:
        return 0
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _is_lib_path(path: str) -> bool:
    """True when ``path`` is under a top-level Foundry ``lib/`` directory."""
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized == "lib" or normalized.startswith("lib/")


def _is_sol_path(path: str) -> bool:
    return path.replace("\\", "/").lower().endswith(".sol")


def _load_sol_sources(project_dir: Path) -> dict[str, str]:
    """Read all ``*.sol`` files under ``project_dir`` keyed by relative path."""
    root = project_dir.resolve()
    if not root.is_dir():
        msg = f"project_dir is not a directory: {project_dir}"
        raise FileNotFoundError(msg)

    sources: dict[str, str] = {}
    for path in sorted(root.rglob("*.sol")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            sources[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            sources[rel] = path.read_text(encoding="utf-8", errors="replace")
    return sources


def _pragma_hint(sources: Mapping[str, str]) -> str | None:
    """Derive a short version string for UI / pricing without failing hard."""
    try:
        info = analyze_pragmas(dict(sources))
    except PragmaConflictError as exc:
        return f"conflict: {exc}"
    if info.solc_version:
        return info.solc_version
    if info.version_range:
        return info.version_range
    if info.raw_pragmas:
        return info.raw_pragmas[0]
    return None


def compute_metrics(
    sources: Mapping[str, str] | None = None,
    project_dir: Path | None = None,
) -> MetricsResult:
    """Compute pre-flight metrics from an in-memory map and/or on-disk project.

    Parameters
    ----------
    sources:
        Relative path → file text. Only ``.sol`` entries contribute to LOC /
        complexity / tokens. When ``None``, sources are loaded from
        ``project_dir``.
    project_dir:
        Optional Foundry-style project root. Used when ``sources`` is ``None``
        to discover ``*.sol`` files. When both are provided, ``sources`` wins
        (disk is not re-read).

    Raises
    ------
    ValueError
        If neither ``sources`` nor ``project_dir`` is provided.
    FileNotFoundError
        If ``project_dir`` is set, ``sources`` is ``None``, and the path is
        missing or not a directory.
    """
    if sources is None and project_dir is None:
        msg = "compute_metrics requires sources and/or project_dir"
        raise ValueError(msg)

    if sources is None:
        assert project_dir is not None  # for type checkers
        resolved = _load_sol_sources(project_dir)
    else:
        resolved = {k: v for k, v in sources.items() if _is_sol_path(k)}

    loc_total = 0
    loc_src_only = 0
    cyclo = 0
    char_total = 0
    file_count = 0

    for path, content in resolved.items():
        file_count += 1
        loc = count_loc(content)
        loc_total += loc
        if not _is_lib_path(path):
            loc_src_only += loc
        cyclo += approx_cyclomatic(content)
        char_total += len(content)

    # Token estimate from total character count (same as chars/4 on concat).
    tokens = (char_total + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN if char_total else 0

    return MetricsResult(
        loc_total=loc_total,
        loc_src_only=loc_src_only,
        file_count=file_count,
        approx_cyclomatic=cyclo,
        approx_tokens=tokens,
        tools_available=probe_tools(),
        pragma_hint=_pragma_hint(resolved),
    )


def metrics_to_dict(m: MetricsResult) -> dict[str, Any]:
    """Serialize :class:`MetricsResult` to a JSON-friendly dict."""
    return asdict(m)


def run_metrics_from_request(sources: Mapping[str, str]) -> dict[str, Any]:
    """Thin helper for a future sync ``POST /metrics`` handler.

    Accepts the same path → content map as audit requests; returns a plain
    dict suitable for JSON responses. Does not start an HTTP server.
    """
    return metrics_to_dict(compute_metrics(sources=sources))
