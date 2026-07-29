"""Compile stage: forge build."""

from __future__ import annotations

import json
from pathlib import Path

from auditor.contracts.enums import JobStage, StageRunStatus
from auditor.contracts.layout import JOB_LAYOUT
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.profiles import STAGE_COMPILE
from auditor.pipeline.registry import StageResult
from auditor.security import CommandTimeoutError, SecurityConfig, run_command


class CompileStage:
    name = STAGE_COMPILE
    job_stage = JobStage.COMPILE
    optional = False

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        if not ctx.project_dir().is_dir():
            return False, "project directory missing"
        return True, None

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        project = ctx.project_dir()
        timeout = float(ctx.meta.get("stage_timeout_seconds") or 120)
        # Optional auto-fix loop
        auto_fix = bool(ctx.options.get("auto_fix_compile")) or _env_flag("AUTO_FIX_COMPILE")
        max_attempts = 1 + (3 if auto_fix else 0)

        log_path = ctx.job_paths.resolve(JOB_LAYOUT.forge_build_log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        last_stderr = b""
        last_stdout = b""
        ok = False

        for attempt in range(1, max_attempts + 1):
            bus.emit(
                ctx.job_id,
                f"Running forge build (attempt {attempt}/{max_attempts})",
                stage=JobStage.COMPILE,
            )
            try:
                result = run_command(
                    ["forge", "build"],
                    timeout_seconds=min(timeout, 180),
                    config=SecurityConfig(
                        timeout_seconds=int(min(timeout, 180)),
                        memory_limit_bytes=None,
                        rlimit_cpu_seconds=None,
                        term_grace_seconds=ctx.security.term_grace_seconds,
                    ),
                    cwd=project,
                )
            except CommandTimeoutError as exc:
                log_path.write_bytes((exc.stdout or b"") + b"\n" + (exc.stderr or b""))
                return StageResult(
                    status=StageRunStatus.TIMED_OUT,
                    message="forge build timed out",
                    hard_fail=True,
                    artifact_paths=(JOB_LAYOUT.forge_build_log,),
                )

            last_stdout, last_stderr = result.stdout, result.stderr
            log_path.write_bytes(result.stdout + b"\n" + result.stderr)
            if result.ok:
                ok = True
                break

            if attempt < max_attempts and auto_fix:
                from auditor.pipeline.stages.autofix import try_auto_fix_compile

                fixed = try_auto_fix_compile(
                    ctx,
                    bus,
                    compiler_log=(result.stdout + result.stderr).decode("utf-8", errors="replace"),
                )
                if not fixed:
                    break
            else:
                break

        if not ok:
            # Attempt auto-fix only if enabled and we have budget — already looped
            return StageResult(
                status=StageRunStatus.FAILED,
                message=_summarize_forge_error(last_stderr or last_stdout),
                hard_fail=True,
                artifact_paths=(JOB_LAYOUT.forge_build_log,),
            )

        # Capture ABIs / bytecode from out/
        captured = _capture_build_artifacts(ctx, project)
        ctx.meta["compile"] = {
            "ok": True,
            "artifacts": captured,
            "solc": _read_solc_hint(project),
        }
        bus.emit(
            ctx.job_id, f"Compile succeeded ({len(captured)} artifacts)", stage=JobStage.COMPILE
        )
        paths = [JOB_LAYOUT.forge_build_log, *captured]
        return StageResult(
            status=StageRunStatus.COMPLETED,
            message="forge build ok",
            artifact_paths=tuple(paths),
        )


def _capture_build_artifacts(ctx: JobContext, project: Path) -> list[str]:
    out = project / "out"
    abi_dir = ctx.job_paths.resolve(JOB_LAYOUT.abi_dir)
    bc_dir = ctx.job_paths.resolve(JOB_LAYOUT.bytecode_dir)
    abi_dir.mkdir(parents=True, exist_ok=True)
    bc_dir.mkdir(parents=True, exist_ok=True)
    rels: list[str] = []
    if not out.is_dir():
        return rels
    for path in out.rglob("*.json"):
        if path.name.endswith(".abi.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        abi = data.get("abi")
        bytecode = (data.get("bytecode") or {}).get("object") or data.get("bytecode")
        deployed = (data.get("deployedBytecode") or {}).get("object") or data.get(
            "deployedBytecode"
        )
        stem = path.stem
        if abi is not None:
            dest = abi_dir / f"{stem}.json"
            dest.write_text(json.dumps(abi, indent=2), encoding="utf-8")
            rels.append(f"{JOB_LAYOUT.abi_dir}/{stem}.json")
        if isinstance(bytecode, str) and bytecode.startswith("0x"):
            dest = bc_dir / f"{stem}.creation.hex"
            dest.write_text(bytecode, encoding="utf-8")
            rels.append(f"{JOB_LAYOUT.bytecode_dir}/{stem}.creation.hex")
        if isinstance(deployed, str) and deployed.startswith("0x"):
            dest = bc_dir / f"{stem}.runtime.hex"
            dest.write_text(deployed, encoding="utf-8")
            rels.append(f"{JOB_LAYOUT.bytecode_dir}/{stem}.runtime.hex")
    return rels


def _read_solc_hint(project: Path) -> str | None:
    toml = project / "foundry.toml"
    if not toml.is_file():
        return None
    for line in toml.read_text(encoding="utf-8").splitlines():
        if "solc_version" in line and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _summarize_forge_error(blob: bytes) -> str:
    text = blob.decode("utf-8", errors="replace").strip()
    if not text:
        return "forge build failed"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "forge build failed: " + " | ".join(lines[-5:])[:500]


def _env_flag(name: str) -> bool:
    import os

    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
