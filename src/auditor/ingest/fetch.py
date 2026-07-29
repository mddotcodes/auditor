"""GitHub Gist source fetch with explicit network opt-in.

Network is **off by default**. Callers must pass
:attr:`~auditor.security.config.NetworkPolicy.ALLOW_FETCH` for the fetch phase
only; later pipeline stages should use :attr:`~auditor.security.config.NetworkPolicy.DENY`
and consume the returned ``sources`` map (or the on-disk cache under ``dest_dir``).

Uses stdlib :mod:`urllib` only (no httpx/requests).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

from auditor.ingest.errors import PayloadTooLargeError
from auditor.ingest.limits import DEFAULT_MAX_FILE_BYTES, DEFAULT_MAX_SOURCE_BYTES
from auditor.security.config import NetworkPolicy

# ---------------------------------------------------------------------------
# Defaults (overridable via env or call kwargs)
# Aligned with IngestLimits so a successful fetch can materialize offline.
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS: Final[float] = 30.0
DEFAULT_MAX_TOTAL_BYTES: Final[int] = DEFAULT_MAX_SOURCE_BYTES
USER_AGENT: Final[str] = "auditor-engine"
GITHUB_API_ACCEPT: Final[str] = "application/vnd.github+json"
GITHUB_API_VERSION: Final[str] = "2022-11-28"
CACHE_META_NAME: Final[str] = ".gist_cache.json"

# Extra budget for JSON envelope around file bodies on the API response.
_RESPONSE_OVERHEAD_BYTES: Final[int] = 256 * 1024

_GIST_ID_RE = re.compile(r"^[0-9a-fA-F]{1,64}$")
_HEX_SEGMENT = re.compile(r"^[0-9a-fA-F]+$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FetchError(Exception):
    """Base class for gist fetch failures."""


class FetchNotAllowedError(FetchError):
    """Raised when network policy does not allow remote fetch (fail closed)."""

    def __init__(self, policy: NetworkPolicy) -> None:
        self.policy = policy
        super().__init__(
            f"Gist fetch denied: network_policy={policy.value!r} "
            f"(require {NetworkPolicy.ALLOW_FETCH.value!r} for the fetch phase)"
        )


class InvalidGistUrlError(FetchError):
    """Raised when a gist URL cannot be parsed to a gist id."""

    def __init__(self, gist_url: str, reason: str = "unrecognized gist URL") -> None:
        self.gist_url = gist_url
        super().__init__(f"{reason}: {gist_url!r}")


class GistFetchError(FetchError):
    """Raised on HTTP/API failures while contacting GitHub."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        msg = f"Environment variable {name}={raw!r} is not a valid float"
        raise ValueError(msg) from exc


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"Environment variable {name}={raw!r} is not a valid integer"
        raise ValueError(msg) from exc


def resolve_fetch_timeout(timeout_seconds: float | None = None) -> float:
    """Resolve timeout: explicit kwarg → ``AUDIT_GIST_FETCH_TIMEOUT_SECONDS`` → default."""
    if timeout_seconds is not None:
        if timeout_seconds <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)
        return timeout_seconds
    value = _env_float("AUDIT_GIST_FETCH_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    if value <= 0:
        msg = "AUDIT_GIST_FETCH_TIMEOUT_SECONDS must be positive"
        raise ValueError(msg)
    return value


def resolve_max_total_bytes(max_total_bytes: int | None = None) -> int:
    """Resolve total size cap: explicit kwarg → ``AUDIT_GIST_MAX_BYTES`` → default."""
    if max_total_bytes is not None:
        if max_total_bytes <= 0:
            msg = "max_total_bytes must be positive"
            raise ValueError(msg)
        return max_total_bytes
    value = _env_int("AUDIT_GIST_MAX_BYTES", DEFAULT_MAX_TOTAL_BYTES)
    if value <= 0:
        msg = "AUDIT_GIST_MAX_BYTES must be positive"
        raise ValueError(msg)
    return value


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


def parse_gist_id(gist_url: str) -> str:
    """Extract a GitHub gist id from common URL shapes.

    Accepted forms (http or https):

    * ``https://gist.github.com/{user}/{id}``
    * ``https://gist.github.com/{user}/{id}/``
    * ``https://gist.github.com/{user}/{id}/raw`` (and deeper raw paths)
    * ``https://gist.github.com/{id}``
    * ``https://gist.githubusercontent.com/{user}/{id}/raw/...``
    * bare hex gist id (no scheme)

    Raises:
        InvalidGistUrlError: if the URL is empty or not a recognized gist URL.
    """
    raw = (gist_url or "").strip()
    if not raw:
        raise InvalidGistUrlError(gist_url, "empty gist URL")

    # Bare id convenience for tests / internal callers.
    if _GIST_ID_RE.fullmatch(raw) and not raw.startswith(("http://", "https://")):
        return raw.lower()

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidGistUrlError(gist_url, "gist URL must be http(s)")

    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").strip("/")
    segments = [s for s in path.split("/") if s]

    if host == "gist.github.com":
        # /{id} or /{user}/{id}[/raw[/...]]
        if len(segments) == 1 and _HEX_SEGMENT.fullmatch(segments[0]):
            return segments[0].lower()
        if len(segments) >= 2 and _HEX_SEGMENT.fullmatch(segments[1]):
            return segments[1].lower()
        raise InvalidGistUrlError(gist_url, "could not find gist id in gist.github.com path")

    if host == "gist.githubusercontent.com":
        # /{user}/{id}/raw/...
        if len(segments) >= 2 and _HEX_SEGMENT.fullmatch(segments[1]):
            return segments[1].lower()
        raise InvalidGistUrlError(
            gist_url, "could not find gist id in gist.githubusercontent.com path"
        )

    if host == "api.github.com":
        # /gists/{id}
        if len(segments) >= 2 and segments[0] == "gists" and _HEX_SEGMENT.fullmatch(segments[1]):
            return segments[1].lower()
        raise InvalidGistUrlError(gist_url, "could not find gist id in api.github.com path")

    raise InvalidGistUrlError(gist_url, f"unsupported host {host!r}")


def _normalize_source_path(filename: str) -> str:
    """Map a gist filename to a relative sources-map key.

    Bare names (no ``/``) are placed under ``src/`` so they materialize as
    Foundry-friendly paths (e.g. ``Foo.sol`` → ``src/Foo.sol``).
    """
    name = filename.replace("\\", "/").strip()
    if not name or name.startswith("/"):
        msg = f"invalid gist filename: {filename!r}"
        raise GistFetchError(msg)
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        msg = f"invalid gist filename (path escape): {filename!r}"
        raise GistFetchError(msg)
    rel = "/".join(parts)
    if "/" not in rel:
        return f"src/{rel}"
    return rel


# ---------------------------------------------------------------------------
# HTTP (stdlib urllib)
# ---------------------------------------------------------------------------


def _read_limited(stream: Any, max_bytes: int) -> bytes:
    """Read from a file-like object, aborting if more than ``max_bytes`` arrive."""
    chunks: list[bytes] = []
    total = 0
    while True:
        # Read slightly past the limit so we can detect overflow.
        to_read = min(65_536, max_bytes - total + 1)
        if to_read <= 0:
            raise PayloadTooLargeError(
                f"HTTP response exceeds size cap of {max_bytes} bytes",
                limit=max_bytes,
            )
        chunk = stream.read(to_read)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                f"HTTP response exceeds size cap of {max_bytes} bytes",
                limit=max_bytes,
                actual=total,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _http_get(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
    """GET ``url`` with User-Agent / Accept headers and a hard body size cap."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": GITHUB_API_ACCEPT,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
            return _read_limited(resp, max_bytes)
    except PayloadTooLargeError:
        raise
    except urllib.error.HTTPError as exc:
        body_preview = ""
        try:
            body_preview = exc.read(512).decode("utf-8", errors="replace")
        except Exception:
            body_preview = ""
        detail = f": {body_preview}" if body_preview else ""
        raise GistFetchError(
            f"GitHub HTTP {exc.code} for {url}{detail}",
            status=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise GistFetchError(f"network error fetching {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise GistFetchError(f"timeout fetching {url} after {timeout_seconds:g}s") from exc


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_meta_path(dest_dir: Path) -> Path:
    return dest_dir / CACHE_META_NAME


def _load_cache_if_complete(dest_dir: Path, gist_id: str) -> dict[str, str] | None:
    """Return sources from ``dest_dir`` when meta + all files are present for ``gist_id``."""
    meta_path = _cache_meta_path(dest_dir)
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(meta, dict):
        return None
    if meta.get("gist_id") != gist_id:
        return None
    files = meta.get("files")
    if not isinstance(files, list) or not files:
        return None

    sources: dict[str, str] = {}
    for rel in files:
        if not isinstance(rel, str):
            return None
        path = dest_dir / rel
        if not path.is_file():
            return None
        try:
            sources[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
    return sources


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via temp file + ``os.replace`` (same-dir atomic)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".partial", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def _write_cache(dest_dir: Path, gist_id: str, sources: dict[str, str]) -> None:
    """Persist sources under ``dest_dir`` and a small completeness meta file."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in sources.items():
        _atomic_write_text(dest_dir / rel, content)
    meta = {
        "gist_id": gist_id,
        "files": sorted(sources.keys()),
        "schema": 1,
    }
    _atomic_write_text(
        _cache_meta_path(dest_dir),
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
    )


# ---------------------------------------------------------------------------
# Gist JSON → sources map
# ---------------------------------------------------------------------------


def _extract_sources_from_gist_json(
    payload: dict[str, Any],
    *,
    max_total_bytes: int,
    max_file_bytes: int,
    timeout_seconds: float,
    fetch_truncated: bool,
) -> dict[str, str]:
    files_obj = payload.get("files")
    if not isinstance(files_obj, dict) or not files_obj:
        raise GistFetchError("gist has no files")

    # Pre-check declared sizes when present.
    declared_total = 0
    for entry in files_obj.values():
        if not isinstance(entry, dict):
            continue
        size = entry.get("size")
        if isinstance(size, int):
            if size > max_file_bytes:
                raise PayloadTooLargeError(
                    f"gist file exceeds per-file cap of {max_file_bytes} bytes (file size={size})",
                    limit=max_file_bytes,
                    actual=size,
                )
            declared_total += size
    if declared_total > max_total_bytes:
        raise PayloadTooLargeError(
            f"gist total size exceeds cap of {max_total_bytes} bytes "
            f"(declared total={declared_total})",
            limit=max_total_bytes,
            actual=declared_total,
        )

    sources: dict[str, str] = {}
    total = 0
    for _key, entry in files_obj.items():
        if not isinstance(entry, dict):
            raise GistFetchError("malformed gist files entry")
        filename = entry.get("filename")
        if not isinstance(filename, str) or not filename:
            raise GistFetchError("gist file missing filename")
        rel = _normalize_source_path(filename)

        content: str | None = None
        truncated = bool(entry.get("truncated"))
        raw_content = entry.get("content")
        if isinstance(raw_content, str) and not truncated:
            content = raw_content
        elif truncated or raw_content is None:
            if not fetch_truncated:
                raise GistFetchError(
                    f"gist file {filename!r} is truncated and raw fetch is disabled"
                )
            raw_url = entry.get("raw_url")
            if not isinstance(raw_url, str) or not raw_url.startswith("https://"):
                raise GistFetchError(f"gist file {filename!r} truncated without https raw_url")
            # Bound raw download to remaining budget + per-file cap.
            remaining = max_total_bytes - total
            cap = min(max_file_bytes, remaining if remaining > 0 else 0)
            if cap <= 0:
                raise PayloadTooLargeError(
                    f"gist total size exceeds cap of {max_total_bytes} bytes",
                    limit=max_total_bytes,
                )
            body = _http_get(raw_url, timeout_seconds=timeout_seconds, max_bytes=cap)
            try:
                content = body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GistFetchError(f"gist file {filename!r} is not valid UTF-8") from exc
        else:
            raise GistFetchError(f"gist file {filename!r} has no usable content")

        encoded_len = len(content.encode("utf-8"))
        if encoded_len > max_file_bytes:
            raise PayloadTooLargeError(
                f"gist file {filename!r} exceeds per-file cap of {max_file_bytes} bytes "
                f"(size={encoded_len})",
                limit=max_file_bytes,
                actual=encoded_len,
            )
        total += encoded_len
        if total > max_total_bytes:
            raise PayloadTooLargeError(
                f"gist total size exceeds cap of {max_total_bytes} bytes (actual>={total})",
                limit=max_total_bytes,
                actual=total,
            )
        if rel in sources:
            raise GistFetchError(f"duplicate source path after normalization: {rel!r}")
        sources[rel] = content

    if not sources:
        raise GistFetchError("gist produced no source files")
    return sources


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_gist_sources(
    gist_url: str,
    *,
    dest_dir: Path,
    network_policy: NetworkPolicy,
    timeout_seconds: float | None = None,
    max_total_bytes: int | None = None,
    max_file_bytes: int | None = None,
) -> dict[str, str]:
    """Fetch a GitHub Gist into a sources map and cache it under ``dest_dir``.

    Behavior:

    1. Parse ``gist_url`` to a gist id (no network).
    2. If ``dest_dir`` already holds a complete cache for that id, return it
       **without** requiring :attr:`~auditor.security.config.NetworkPolicy.ALLOW_FETCH`
       (supports fetch-once, then deny-network pipeline phases).
    3. If cache miss and ``network_policy`` is not
       :attr:`~auditor.security.config.NetworkPolicy.ALLOW_FETCH`, raise
       :class:`FetchNotAllowedError` (fail closed; **no** network attempt).
    4. ``GET https://api.github.com/gists/{id}`` via stdlib urllib with timeout
       and response size cap.
    5. Map gist files to relative paths (bare names under ``src/``), enforce
       per-file and total byte caps, write cache atomically, return
       ``dict[str, str]`` suitable for materialization (M3.1).

    Env knobs (used when kwargs are omitted):

    * ``AUDIT_GIST_FETCH_TIMEOUT_SECONDS`` (default ``30``)
    * ``AUDIT_GIST_MAX_BYTES`` (default ``2097152`` total source bytes)

    Orchestrators should set ``network_policy=ALLOW_FETCH`` **only** for this
    phase; default job policy remains ``DENY``.

    Args:
        gist_url: Gist URL or bare hex id.
        dest_dir: Cache directory (e.g. ``job_paths.resolve("artifacts/source/gist")``).
        network_policy: Must be ``ALLOW_FETCH`` when a network fetch is required.
        timeout_seconds: Per-request timeout; ``None`` → env / default.
        max_total_bytes: Total UTF-8 source byte cap; ``None`` → env / default.
        max_file_bytes: Per-file cap (default = ``DEFAULT_MAX_FILE_BYTES``).

    Returns:
        Map of relative path → source text.

    Raises:
        FetchNotAllowedError: Policy is not ``ALLOW_FETCH`` and cache is incomplete.
        InvalidGistUrlError: URL cannot be parsed.
        PayloadTooLargeError: Size caps exceeded.
        GistFetchError: HTTP/API/content errors.
        ValueError: Invalid timeout / size configuration.
    """
    gist_id = parse_gist_id(gist_url)
    dest = Path(dest_dir)

    cached = _load_cache_if_complete(dest, gist_id)
    if cached is not None:
        return cached

    if network_policy is not NetworkPolicy.ALLOW_FETCH:
        raise FetchNotAllowedError(network_policy)

    timeout = resolve_fetch_timeout(timeout_seconds)
    total_cap = resolve_max_total_bytes(max_total_bytes)
    file_cap = (
        max_file_bytes if max_file_bytes is not None else min(DEFAULT_MAX_FILE_BYTES, total_cap)
    )
    if file_cap <= 0:
        msg = "max_file_bytes must be positive"
        raise ValueError(msg)

    api_url = f"https://api.github.com/gists/{gist_id}"
    response_cap = total_cap + _RESPONSE_OVERHEAD_BYTES
    body = _http_get(api_url, timeout_seconds=timeout, max_bytes=response_cap)

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GistFetchError("gist API response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise GistFetchError("gist API response must be a JSON object")

    sources = _extract_sources_from_gist_json(
        payload,
        max_total_bytes=total_cap,
        max_file_bytes=file_cap,
        timeout_seconds=timeout,
        fetch_truncated=True,
    )
    _write_cache(dest, gist_id, sources)
    return sources
