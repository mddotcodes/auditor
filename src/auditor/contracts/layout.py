"""Deterministic on-disk layout for a job workspace.

All paths are relative to the job root: ``{AUDIT_JOB_ROOT}/{job_id}/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


@dataclass(frozen=True, slots=True)
class JobLayout:
    """Path constants (posix-style relative segments)."""

    project: str = "project"
    artifacts: str = "artifacts"
    events_log: str = "artifacts/events.jsonl"
    manifest: str = "artifacts/manifest.json"
    pipeline_meta: str = "artifacts/meta/pipeline.json"
    compile_dir: str = "artifacts/compile"
    forge_build_log: str = "artifacts/compile/forge-build.log"
    bytecode_dir: str = "artifacts/compile/bytecode"
    abi_dir: str = "artifacts/compile/abi"
    static_dir: str = "artifacts/static"
    slither_raw: str = "artifacts/static/slither.json"
    aderyn_raw: str = "artifacts/static/aderyn.json"
    metamorphic_raw: str = "artifacts/static/metamorphic.json"
    findings: str = "artifacts/static/findings.json"
    llm_tests_dir: str = "artifacts/llm_tests"
    generated_tests_dir: str = "artifacts/llm_tests/generated"
    fuzz_dir: str = "artifacts/fuzz"
    forge_test_json: str = "artifacts/fuzz/forge-test.json"
    forge_test_log: str = "artifacts/fuzz/forge-test.log"
    coverage_dir: str = "artifacts/fuzz/coverage"
    fingerprint: str = "artifacts/fingerprint.json"
    source_snapshot: str = "artifacts/source"


JOB_LAYOUT: Final[JobLayout] = JobLayout()


@dataclass(frozen=True, slots=True)
class JobPaths:
    """Resolved absolute paths for one job id under a job root."""

    job_root: Path
    job_id: str

    @property
    def base(self) -> Path:
        return self.job_root / self.job_id

    def resolve(self, relative: str) -> Path:
        """Resolve a layout-relative path under the job base (no ``..`` escape)."""
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            msg = f"refusing unsafe relative path: {relative!r}"
            raise ValueError(msg)
        return self.base / rel

    @property
    def project(self) -> Path:
        return self.resolve(JOB_LAYOUT.project)

    @property
    def artifacts(self) -> Path:
        return self.resolve(JOB_LAYOUT.artifacts)

    @property
    def manifest(self) -> Path:
        return self.resolve(JOB_LAYOUT.manifest)

    @property
    def events_log(self) -> Path:
        return self.resolve(JOB_LAYOUT.events_log)

    def ensure_skeleton(self) -> None:
        """Create the standard directory tree (idempotent)."""
        for rel in (
            JOB_LAYOUT.project,
            JOB_LAYOUT.compile_dir,
            JOB_LAYOUT.bytecode_dir,
            JOB_LAYOUT.abi_dir,
            JOB_LAYOUT.static_dir,
            JOB_LAYOUT.generated_tests_dir,
            JOB_LAYOUT.fuzz_dir,
            JOB_LAYOUT.coverage_dir,
            JOB_LAYOUT.source_snapshot,
            "artifacts/meta",
        ):
            self.resolve(rel).mkdir(parents=True, exist_ok=True)
