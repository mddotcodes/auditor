"""Frozen enumerations shared by OpenAPI, JSON Schema, and runtime models.

Breaking changes to these values require a public API / schema version bump.
"""

from __future__ import annotations

from enum import StrEnum


class SchemaVersion(StrEnum):
    """Contract document version embedded in payloads."""

    V1 = "1"


class JobStatus(StrEnum):
    """Lifecycle status of an audit job (REST ``GET /v1/jobs/{id}``)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.TIMED_OUT,
            JobStatus.CANCELLED,
        }


class JobStage(StrEnum):
    """Pipeline stage currently executing (or last active).

    Order for a full run::

        materialize → compile → static → llm_tests → fuzz → finalize

    ``llm_tests`` and ``fuzz`` may be skipped when no LLM key / static-only mode.
    """

    MATERIALIZE = "materialize"
    COMPILE = "compile"
    STATIC = "static"
    LLM_TESTS = "llm_tests"
    FUZZ = "fuzz"
    FINALIZE = "finalize"


class StageRunStatus(StrEnum):
    """Per-stage outcome recorded in the artifact manifest."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"


class EventLevel(StrEnum):
    """Severity of a job event stream message."""

    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class TerminalReason(StrEnum):
    """Why a job ended; present on the final event(s)."""

    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ArtifactKind(StrEnum):
    """Logical type of a file listed in ``manifest.json``."""

    MANIFEST = "manifest"
    PIPELINE_META = "pipeline_meta"
    FORGE_BUILD_LOG = "forge_build_log"
    FORGE_TEST_LOG = "forge_test_log"
    FORGE_TEST_JSON = "forge_test_json"
    SLITHER_RAW = "slither_raw"
    FINDINGS = "findings"
    GENERATED_TEST = "generated_test"
    COVERAGE = "coverage"
    BYTECODE = "bytecode"
    ABI = "abi"
    FINGERPRINT = "fingerprint"
    SOURCE_SNAPSHOT = "source_snapshot"
    EVENTS_LOG = "events_log"
    OTHER = "other"
