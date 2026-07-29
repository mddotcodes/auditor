"""Source ingestion: remote fetch, path/size validation, Foundry materialization."""

from __future__ import annotations

from auditor.ingest.errors import (
    InvalidFoundryConfigError,
    MaterializeError,
    PathTraversalError,
    PayloadTooLargeError,
    PragmaConflictError,
)
from auditor.ingest.fetch import (
    DEFAULT_MAX_TOTAL_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    FetchError,
    FetchNotAllowedError,
    GistFetchError,
    InvalidGistUrlError,
    fetch_gist_sources,
    parse_gist_id,
    resolve_fetch_timeout,
    resolve_max_total_bytes,
)
from auditor.ingest.limits import IngestLimits
from auditor.ingest.materialize import MaterializeResult, materialize_sources
from auditor.ingest.pragma import PragmaInfo

__all__ = [
    "DEFAULT_MAX_TOTAL_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "FetchError",
    "FetchNotAllowedError",
    "GistFetchError",
    "IngestLimits",
    "InvalidFoundryConfigError",
    "InvalidGistUrlError",
    "MaterializeError",
    "MaterializeResult",
    "PathTraversalError",
    "PayloadTooLargeError",
    "PragmaConflictError",
    "PragmaInfo",
    "fetch_gist_sources",
    "materialize_sources",
    "parse_gist_id",
    "resolve_fetch_timeout",
    "resolve_max_total_bytes",
]
