"""Optional Echidna property fuzz (deep profile)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from auditor.contracts.enums import JobStage, StageRunStatus
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.profiles import STAGE_ECHIDNA, AuditProfile
from auditor.pipeline.registry import StageResult
from auditor.security import CommandTimeoutError, SecurityConfig, run_command


class EchidnaStage:
    name = STAGE_ECHIDNA
    job_stage = JobStage.ECHIDNA
    optional = True

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        if ctx.profile is not AuditProfile.DEEP and not _env_flag("AUDIT_ENABLE_ECHIDNA"):
            return False, "echidna only in deep profile or AUDIT_ENABLE_ECHIDNA"
        if shutil.which("echidna") is None and shutil.which("echidna-test") is None:
            return False, "echidna binary not installed"
        if ctx.hard_fail:
            return False, "skipped after hard failure"
        if not _has_properties(ctx.project_dir()):
            return False, "no Echidna property contracts detected"
        return True, None

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        project = ctx.project_dir()
        binary = shutil.which("echidna") or shutil.which("echidna-test") or "echidna"
        timeout = min(float(ctx.meta.get("stage_timeout_seconds") or 60), 90)
        out_dir = ctx.job_paths.resolve("artifacts/fuzz")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_log = out_dir / "echidna.log"
        bus.emit(ctx.job_id, "Running Echidna", stage=JobStage.ECHIDNA)
        # Target contract dir; echidna CLI varies by version
        cmd = [binary, str(project), "--contract", _guess_contract(project), "--test-limit", "200"]
        try:
            result = run_command(
                cmd,
                timeout_seconds=timeout,
                config=SecurityConfig(
                    timeout_seconds=int(timeout),
                    memory_limit_bytes=None,
                    rlimit_cpu_seconds=None,
                ),
                cwd=project,
            )
        except CommandTimeoutError as exc:
            out_log.write_bytes((exc.stdout or b"") + b"\n" + (exc.stderr or b""))
            return StageResult(
                status=StageRunStatus.TIMED_OUT,
                message="echidna timed out",
                hard_fail=False,
                artifact_paths=("artifacts/fuzz/echidna.log",),
            )
        out_log.write_bytes(result.stdout + b"\n" + result.stderr)
        status = StageRunStatus.COMPLETED if result.ok else StageRunStatus.FAILED
        return StageResult(
            status=status,
            message="echidna finished" if result.ok else "echidna found failing properties",
            hard_fail=False,
            artifact_paths=("artifacts/fuzz/echidna.log",),
        )


def _has_properties(project: Path) -> bool:
    for path in project.rglob("*.sol"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "echidna_" in text or "invariant_" in text:
            return True
    return False


def _guess_contract(project: Path) -> str:
    for path in project.rglob("*Echidna*.sol"):
        return path.stem
    for path in project.rglob("*.sol"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "echidna_" in text:
            return path.stem
    return "InvariantsEchidna"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
