"""Optional Mythril symbolic plugin (disabled by default)."""

from __future__ import annotations

import os
import shutil

from auditor.contracts.enums import JobStage, StageRunStatus
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.findings import Finding, FindingSeverity
from auditor.pipeline.registry import StageResult
from auditor.security import CommandTimeoutError, SecurityConfig, run_command


class MythrilStage:
    name = "mythril"
    job_stage = JobStage.STATIC  # reuse static bucket for events if no dedicated enum path
    optional = True

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        if not _env_flag("AUDIT_ENABLE_MYTHRIL"):
            return False, "AUDIT_ENABLE_MYTHRIL not set"
        if shutil.which("myth") is None and shutil.which("mythril") is None:
            return False, "mythril binary not installed"
        if ctx.hard_fail:
            return False, "skipped after hard failure"
        return True, None

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        project = ctx.project_dir()
        binary = shutil.which("myth") or shutil.which("mythril") or "myth"
        timeout = min(float(os.environ.get("AUDIT_MYTHRIL_TIMEOUT", "60")), 90)
        out = ctx.job_paths.resolve("artifacts/static/mythril.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        # Analyze first src file as a bounded demo
        targets = list((project / "src").rglob("*.sol")) if (project / "src").is_dir() else []
        if not targets:
            return StageResult(status=StageRunStatus.SKIPPED, skip_reason="no src for mythril")
        target = targets[0]
        bus.emit(ctx.job_id, f"Running Mythril on {target.name}", stage=JobStage.STATIC)
        cmd = [
            binary,
            "analyze",
            str(target),
            "-o",
            "json",
            "--execution-timeout",
            str(int(timeout)),
        ]
        try:
            result = run_command(
                cmd,
                timeout_seconds=timeout + 5,
                config=SecurityConfig(
                    timeout_seconds=int(timeout + 5),
                    memory_limit_bytes=None,
                    rlimit_cpu_seconds=None,
                ),
                cwd=project,
            )
        except CommandTimeoutError as exc:
            out.write_bytes((exc.stdout or b"") + b"\n" + (exc.stderr or b""))
            return StageResult(
                status=StageRunStatus.TIMED_OUT,
                message="mythril timed out",
                hard_fail=False,
                artifact_paths=("artifacts/static/mythril.json",),
            )
        out.write_bytes(result.stdout or result.stderr or b"{}")
        # Best-effort: count issues if JSON list
        try:
            import json

            data = json.loads((result.stdout or b"{}").decode("utf-8", errors="replace"))
            issues = data if isinstance(data, list) else data.get("issues") or []
            findings = [
                Finding(
                    tool="mythril",
                    detector_id=str(i.get("swc-id") or i.get("title") or "issue"),
                    severity=FindingSeverity.MEDIUM,
                    title=str(i.get("title") or "mythril issue"),
                    description=str(i.get("description") or "")[:2000],
                    raw_ref="artifacts/static/mythril.json",
                )
                for i in issues[:50]
                if isinstance(i, dict)
            ]
            if findings:
                ctx.add_findings(findings)
        except Exception:
            pass
        return StageResult(
            status=StageRunStatus.COMPLETED if result.ok else StageRunStatus.FAILED,
            message="mythril finished",
            hard_fail=False,
            artifact_paths=("artifacts/static/mythril.json",),
        )


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
