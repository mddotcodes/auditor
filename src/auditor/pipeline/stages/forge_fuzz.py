"""Foundry test / fuzz execution stage."""

from __future__ import annotations

import json
import os

from auditor.contracts.enums import JobStage, StageRunStatus
from auditor.contracts.layout import JOB_LAYOUT
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.profiles import STAGE_FORGE_FUZZ, AuditProfile
from auditor.pipeline.registry import StageResult
from auditor.security import CommandTimeoutError, SecurityConfig, run_command


class ForgeFuzzStage:
    name = STAGE_FORGE_FUZZ
    job_stage = JobStage.FUZZ
    optional = True

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        if ctx.profile is AuditProfile.STATIC:
            return False, "static profile skips forge fuzz"
        if ctx.hard_fail:
            return False, "skipped after hard failure"
        project = ctx.project_dir()
        test_dir = project / "test"
        if not test_dir.is_dir() or not any(test_dir.rglob("*.sol")):
            return False, "no tests to run"
        return True, None

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        project = ctx.project_dir()
        fuzz_runs = int(os.environ.get("AUDIT_FOUNDRY_FUZZ_RUNS", "64"))
        timeout = min(float(ctx.meta.get("stage_timeout_seconds") or 120), 180)
        out_json = ctx.job_paths.resolve(JOB_LAYOUT.forge_test_json)
        out_log = ctx.job_paths.resolve(JOB_LAYOUT.forge_test_log)
        out_json.parent.mkdir(parents=True, exist_ok=True)

        bus.emit(
            ctx.job_id,
            f"Running forge test (fuzz_runs={fuzz_runs})",
            stage=JobStage.FUZZ,
        )
        cmd = [
            "forge",
            "test",
            "--json",
            "-vv",
            "--fuzz-runs",
            str(fuzz_runs),
        ]
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
                env={
                    **os.environ,
                    "FOUNDRY_INVARIANT_RUNS": os.environ.get("AUDIT_FOUNDRY_INVARIANT_RUNS", "16"),
                },
            )
        except CommandTimeoutError as exc:
            out_log.write_bytes((exc.stdout or b"") + b"\n" + (exc.stderr or b""))
            return StageResult(
                status=StageRunStatus.TIMED_OUT,
                message="forge test timed out",
                hard_fail=False,
                artifact_paths=(JOB_LAYOUT.forge_test_log,),
            )

        out_log.write_bytes(result.stdout + b"\n" + result.stderr)
        # forge --json may print JSON lines; store best-effort
        text = result.stdout.decode("utf-8", errors="replace")
        parsed = _try_parse_json_blob(text)
        out_json.write_text(
            json.dumps(parsed if parsed is not None else {"raw": text[-50000:]}, indent=2),
            encoding="utf-8",
        )
        from auditor.pipeline.forge_summary import summarize_forge_json

        summary = summarize_forge_json(parsed)
        summary["returncode"] = result.returncode
        ctx.meta["forge_test"] = summary
        status = StageRunStatus.COMPLETED if result.ok else StageRunStatus.FAILED
        if summary.get("total", 0) > 0:
            msg = f"forge test: {summary['passed']} passed, {summary['failed']} failed" + (
                f" — {', '.join(summary['failed_names'][:3])}"
                if summary.get("failed_names")
                else ""
            )
            if result.ok:
                msg = f"forge test passed ({summary['passed']} tests)"
        else:
            msg = "forge test passed" if result.ok else "forge test failed (see artifacts)"
        bus.emit(
            ctx.job_id,
            msg,
            stage=JobStage.FUZZ,
            data={"forge_summary": summary},
        )
        return StageResult(
            status=status,
            message=msg,
            hard_fail=False,
            artifact_paths=(JOB_LAYOUT.forge_test_json, JOB_LAYOUT.forge_test_log),
        )


def _try_parse_json_blob(text: str) -> object | None:
    text = text.strip()
    if not text:
        return None
    try:
        parsed: object = json.loads(text)
        return parsed
    except json.JSONDecodeError:
        pass
    # last JSON object in output
    for i in range(len(text) - 1, -1, -1):
        if text[i] == "{":
            try:
                parsed = json.loads(text[i:])
                return parsed
            except json.JSONDecodeError:
                continue
    return None
