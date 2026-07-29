"""Static analysis stage: Slither ∥ Aderyn → normalized findings."""

from __future__ import annotations

import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auditor.contracts.enums import JobStage, StageRunStatus
from auditor.contracts.layout import JOB_LAYOUT
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.findings import Finding, FindingsDocument, dedup_findings
from auditor.pipeline.profiles import STAGE_STATIC
from auditor.pipeline.registry import StageResult
from auditor.pipeline.stages.static_parsers import parse_aderyn_json, parse_slither_json
from auditor.security import CommandTimeoutError, SecurityConfig, run_command

logger = logging.getLogger(__name__)

ADERYN_RAW = JOB_LAYOUT.aderyn_raw
SLITHER_RAW = JOB_LAYOUT.slither_raw
FINDINGS_PATH = JOB_LAYOUT.findings


@dataclass
class _ToolOutcome:
    tool: str
    ok: bool
    findings: list[Finding] = field(default_factory=list)
    artifact: str | None = None
    error: str | None = None
    skipped: bool = False


class StaticAnalysisStage:
    """Run Slither and Aderyn in parallel; soft-fail on tool crashes."""

    name = STAGE_STATIC
    job_stage = JobStage.STATIC
    optional = True  # soft-fail: job continues if tools crash

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        if not ctx.project_dir().is_dir():
            return False, "project directory missing"
        return True, None

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        project = ctx.project_dir()
        stage_timeout = float(ctx.meta.get("stage_timeout_seconds", 120) or 120)
        # Split fairly across the two tools (they still run in parallel).
        per_tool_timeout = max(5.0, stage_timeout / 2.0)

        static_dir = ctx.job_paths.resolve(JOB_LAYOUT.static_dir)
        static_dir.mkdir(parents=True, exist_ok=True)

        bus.emit(
            ctx.job_id,
            f"Static analysis: Slither ∥ Aderyn (timeout {per_tool_timeout:g}s each)",
            stage=JobStage.STATIC,
        )

        outcomes: dict[str, _ToolOutcome] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="static") as pool:
            futures = {
                pool.submit(_run_slither, ctx, project, per_tool_timeout): "slither",
                pool.submit(_run_aderyn, ctx, project, per_tool_timeout): "aderyn",
            }
            for fut in as_completed(futures):
                tool = futures[fut]
                try:
                    outcomes[tool] = fut.result()
                except Exception as exc:
                    logger.exception("%s worker crashed", tool)
                    outcomes[tool] = _ToolOutcome(tool=tool, ok=False, error=str(exc))

        # Preserve insertion order for tools_run / tools_failed.
        tools_run: list[str] = []
        tools_failed: list[str] = []
        merged: list[Finding] = []
        artifact_paths: list[str] = []

        for tool in ("slither", "aderyn"):
            out = outcomes.get(tool) or _ToolOutcome(tool=tool, ok=False, error="missing outcome")
            if out.artifact:
                artifact_paths.append(out.artifact)
            if out.ok:
                tools_run.append(tool)
                merged.extend(out.findings)
            else:
                tools_failed.append(tool)
                note = out.error or "failed"
                bus.emit(
                    ctx.job_id,
                    f"{tool}: {note}",
                    stage=JobStage.STATIC,
                )

        deduped = dedup_findings(merged)
        findings_path = ctx.job_paths.resolve(FINDINGS_PATH)
        findings_path.parent.mkdir(parents=True, exist_ok=True)

        # Merge into ctx.findings (stage may re-run or accumulate with metamorphic).
        for t in tools_run:
            if t not in ctx.findings.tools_run:
                ctx.findings.tools_run.append(t)
        for t in tools_failed:
            if t not in ctx.findings.tools_failed:
                ctx.findings.tools_failed.append(t)

        ctx.add_findings(deduped)

        # Write a stage-local findings document (this stage's tools only + any prior).
        doc = FindingsDocument(
            schema_version=ctx.findings.schema_version,
            findings=list(ctx.findings.findings),
            tools_run=list(ctx.findings.tools_run),
            tools_failed=list(ctx.findings.tools_failed),
        )
        findings_path.write_text(
            doc.model_dump_json(indent=2),
            encoding="utf-8",
        )
        if FINDINGS_PATH not in artifact_paths:
            artifact_paths.append(FINDINGS_PATH)

        ctx.meta["static"] = {
            "tools_run": tools_run,
            "tools_failed": tools_failed,
            "finding_count": len(deduped),
            "finding_count_total": len(ctx.findings.findings),
        }

        any_ok = bool(tools_run)
        msg_parts = [
            f"{len(deduped)} finding(s)",
            f"tools_run={tools_run or []}",
        ]
        if tools_failed:
            msg_parts.append(f"tools_failed={tools_failed}")
        message = "static: " + "; ".join(msg_parts)

        bus.emit(ctx.job_id, message, stage=JobStage.STATIC)

        # Soft-fail policy: never hard_fail. COMPLETED if any tool worked or we
        # still produced findings.json; FAILED only when every tool failed.
        status = StageRunStatus.COMPLETED if any_ok or not tools_failed else StageRunStatus.FAILED

        return StageResult(
            status=status,
            message=message,
            hard_fail=False,
            artifact_paths=tuple(artifact_paths),
        )


def _tool_security(ctx: JobContext, timeout: float) -> SecurityConfig:
    return SecurityConfig(
        timeout_seconds=max(1, int(timeout)),
        memory_limit_bytes=None,
        rlimit_cpu_seconds=None,
        term_grace_seconds=ctx.security.term_grace_seconds,
        network_policy=ctx.security.network_policy,
        job_root=ctx.security.job_root,
    )


def _load_json_file(path: Path) -> Any | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _load_json_bytes(blob: bytes) -> Any | None:
    if not blob or not blob.strip():
        return None
    try:
        return json.loads(blob.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def _run_slither(ctx: JobContext, project: Path, timeout: float) -> _ToolOutcome:
    if shutil.which("slither") is None:
        return _ToolOutcome(
            tool="slither",
            ok=False,
            skipped=True,
            error="slither not found on PATH",
        )

    out_path = ctx.job_paths.resolve(SLITHER_RAW)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Prefer writing JSON to the artifact path (absolute so cwd=project is safe).
    cmd = ["slither", ".", "--json", str(out_path)]

    try:
        result = run_command(
            cmd,
            timeout_seconds=timeout,
            config=_tool_security(ctx, timeout),
            cwd=project,
        )
    except CommandTimeoutError as exc:
        # Best-effort: slither may have flushed partial JSON.
        data = _load_json_file(out_path)
        if data is None:
            data = _load_json_bytes(exc.stdout or b"")
        if data is not None:
            _write_raw_if_missing(out_path, data, exc.stdout)
            findings = parse_slither_json(data, raw_ref=SLITHER_RAW)
            return _ToolOutcome(
                tool="slither",
                ok=True,
                findings=findings,
                artifact=SLITHER_RAW,
                error="timed out (partial results kept)",
            )
        return _ToolOutcome(
            tool="slither",
            ok=False,
            error=f"timed out after {timeout:g}s",
            artifact=SLITHER_RAW if out_path.is_file() else None,
        )
    except OSError as exc:
        return _ToolOutcome(tool="slither", ok=False, error=str(exc))

    data = _load_json_file(out_path)
    if data is None:
        data = _load_json_bytes(result.stdout)
        if data is not None:
            out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Slither exits non-zero when findings exist — still treat JSON as success.
    if data is None:
        err = (result.stderr or result.stdout).decode("utf-8", errors="replace")[:300]
        return _ToolOutcome(
            tool="slither",
            ok=False,
            error=f"no JSON output (rc={result.returncode}): {err or 'empty'}",
            artifact=SLITHER_RAW if out_path.is_file() else None,
        )

    # Persist a clean dump even if slither wrote already.
    if not out_path.is_file() or out_path.stat().st_size == 0:
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    findings = parse_slither_json(data, raw_ref=SLITHER_RAW)
    # success:false with error string still means the tool ran; mark failed only
    # when detectors are missing and success is explicitly false.
    if (
        isinstance(data, dict)
        and data.get("success") is False
        and not findings
        and not (isinstance(data.get("results"), dict) and data["results"].get("detectors"))
    ):
        return _ToolOutcome(
            tool="slither",
            ok=False,
            error=str(data.get("error") or "slither reported success=false"),
            artifact=SLITHER_RAW,
        )

    return _ToolOutcome(
        tool="slither",
        ok=True,
        findings=findings,
        artifact=SLITHER_RAW,
    )


def _run_aderyn(ctx: JobContext, project: Path, timeout: float) -> _ToolOutcome:
    if shutil.which("aderyn") is None:
        return _ToolOutcome(
            tool="aderyn",
            ok=False,
            skipped=True,
            error="aderyn not found on PATH",
        )

    out_path = ctx.job_paths.resolve(ADERYN_RAW)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # JSON extension selects JSON report format (Cyfrin Aderyn).
    # --skip-update-check avoids network under network_mode=none.
    attempts: list[list[str]] = [
        [
            "aderyn",
            ".",
            "-o",
            str(out_path),
            "--skip-update-check",
            "--no-snippets",
        ],
        ["aderyn", ".", "-o", str(out_path), "--skip-update-check"],
        ["aderyn", ".", "-o", str(out_path)],
    ]

    last_error = "aderyn failed"
    for cmd in attempts:
        try:
            result = run_command(
                cmd,
                timeout_seconds=timeout,
                config=_tool_security(ctx, timeout),
                cwd=project,
            )
        except CommandTimeoutError as exc:
            data = _load_json_file(out_path)
            if data is None:
                data = _load_json_bytes(exc.stdout or b"")
            if data is not None:
                _write_raw_if_missing(out_path, data, exc.stdout)
                findings = parse_aderyn_json(data, raw_ref=ADERYN_RAW)
                return _ToolOutcome(
                    tool="aderyn",
                    ok=True,
                    findings=findings,
                    artifact=ADERYN_RAW,
                    error="timed out (partial results kept)",
                )
            return _ToolOutcome(
                tool="aderyn",
                ok=False,
                error=f"timed out after {timeout:g}s",
                artifact=ADERYN_RAW if out_path.is_file() else None,
            )
        except OSError as exc:
            return _ToolOutcome(tool="aderyn", ok=False, error=str(exc))

        data = _load_json_file(out_path)
        if data is None:
            data = _load_json_bytes(result.stdout)
            if data is not None:
                out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        if data is not None:
            findings = parse_aderyn_json(data, raw_ref=ADERYN_RAW)
            return _ToolOutcome(
                tool="aderyn",
                ok=True,
                findings=findings,
                artifact=ADERYN_RAW,
            )

        err = (result.stderr or result.stdout).decode("utf-8", errors="replace")[:300]
        last_error = f"no JSON output (rc={result.returncode}): {err or 'empty'}"
        # Unknown flag → try next command shape.
        if result.returncode != 0 and (
            "unexpected" in err.lower() or "unknown" in err.lower() or "unrecognized" in err.lower()
        ):
            continue
        # Non-zero without JSON: stop (findings might use non-zero exit).
        if result.returncode != 0:
            break

    return _ToolOutcome(
        tool="aderyn",
        ok=False,
        error=last_error,
        artifact=ADERYN_RAW if out_path.is_file() else None,
    )


def _write_raw_if_missing(path: Path, data: Any, stdout: bytes | None) -> None:
    if path.is_file() and path.stat().st_size > 0:
        return
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, TypeError):
        if stdout:
            path.write_bytes(stdout)
