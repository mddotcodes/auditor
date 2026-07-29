"""Audit pipeline runner — orchestrates stages under a global timeout budget."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from auditor.contracts.enums import (
    EventLevel,
    JobStage,
    JobStatus,
    StageRunStatus,
    TerminalReason,
)
from auditor.contracts.jobs import AuditOptions, AuditRequest
from auditor.contracts.layout import JobPaths
from auditor.pipeline.context import JobContext, new_job_id
from auditor.pipeline.events import EventBus
from auditor.pipeline.profiles import AuditProfile, stages_for_profile
from auditor.pipeline.registry import Stage, StageRegistry, StageResult
from auditor.pipeline.store import InMemoryJobStore, JobStore
from auditor.security.config import SecurityConfig

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Run materialize→…→finalize for one job at a time (caller may thread)."""

    def __init__(
        self,
        registry: StageRegistry,
        *,
        store: JobStore | None = None,
        bus: EventBus | None = None,
        job_root: Path | None = None,
        security: SecurityConfig | None = None,
    ) -> None:
        self.registry = registry
        self.store = store if store is not None else InMemoryJobStore()
        self.bus = bus if bus is not None else EventBus()
        self.job_root = job_root
        self.security = security

    def submit(
        self,
        request: AuditRequest,
        *,
        profile: AuditProfile | None = None,
        job_id: str | None = None,
        job_root: Path | None = None,
        security: SecurityConfig | None = None,
        run_inline: bool = True,
    ) -> JobContext:
        """Create a job context and optionally run the pipeline immediately."""
        from auditor.pipeline.profiles import profile_from_env

        jid = job_id or new_job_id()
        root = Path(job_root or self.job_root or Path.cwd() / "work" / "jobs")
        paths = JobPaths(job_root=root, job_id=jid)
        paths.ensure_skeleton()
        sec = security or self.security or SecurityConfig.from_env()
        prof = profile if profile is not None else profile_from_env()

        opts = request.options if isinstance(request.options, AuditOptions) else AuditOptions()
        ctx = JobContext(
            job_id=jid,
            job_paths=paths,
            profile=prof,
            security=sec,
            sources=dict(request.sources or {}),
            gist_url=request.gist_url,
            options=opts.model_dump(),
        )
        ctx.ensure_manifest()
        self.store.put(ctx)
        if run_inline:
            self.run(ctx)
        return ctx

    def run(self, ctx: JobContext) -> JobContext:
        """Execute all profile stages. Updates store + events."""
        started = time.monotonic()
        budget = float(ctx.security.timeout_seconds)
        ctx.set_running()
        self.bus.emit(
            ctx.job_id,
            f"Job started (profile={ctx.profile.value})",
            stage=JobStage.MATERIALIZE,
            progress=0,
        )

        stage_names = stages_for_profile(ctx.profile)
        # Optional mythril if enabled
        if _env_flag("AUDIT_ENABLE_MYTHRIL") and "mythril" not in stage_names:
            if stage_names and stage_names[-1] == "finalize":
                stage_names = [*stage_names[:-1], "mythril", "finalize"]
            else:
                stage_names = [*stage_names, "mythril"]

        stages = self.registry.resolve(stage_names)
        n = max(len(stages), 1)
        # Always leave headroom so finalize can write manifests after long LLM runs.
        finalize_reserve = min(45.0, max(15.0, budget * 0.12))

        try:
            for i, stage in enumerate(stages):
                if ctx.cancel_requested:
                    self._run_finalize_only(ctx, stages[i:])
                    self._finish(ctx, TerminalReason.CANCELLED, JobStatus.CANCELLED, "cancelled")
                    return ctx

                elapsed = time.monotonic() - started
                is_finalize = stage.name == "finalize"
                limit = budget if is_finalize else max(0.0, budget - finalize_reserve)
                if elapsed >= limit and not is_finalize:
                    self.bus.emit(
                        ctx.job_id,
                        f"Reserving time for finalize (elapsed {elapsed:.0f}s)",
                        stage=JobStage.FINALIZE,
                        level=EventLevel.WARN,
                    )
                    self._run_finalize_only(ctx, stages[i:])
                    self._finish(
                        ctx,
                        TerminalReason.TIMED_OUT,
                        JobStatus.TIMED_OUT,
                        f"global timeout {budget:g}s",
                    )
                    return ctx
                if elapsed >= budget and is_finalize:
                    # Still try finalize even if slightly over
                    pass

                remaining = max(1.0, (budget if is_finalize else limit) - elapsed)
                progress = int(100 * i / n)
                ctx.set_progress(progress)
                js = stage.job_stage
                ctx.set_running(js)

                should, reason = stage.should_run(ctx)
                if not should:
                    ctx.record_stage(
                        js,
                        StageRunStatus.SKIPPED,
                        message=reason,
                        finished_at=datetime.now(UTC),
                    )
                    self.bus.emit(
                        ctx.job_id,
                        reason or f"Skipping {stage.name}",
                        stage=js,
                        level=EventLevel.INFO,
                        progress=progress,
                        data={"skipped": True},
                    )
                    continue

                self.bus.emit(
                    ctx.job_id,
                    f"Starting stage {stage.name}",
                    stage=js,
                    progress=progress,
                )
                stage_started = datetime.now(UTC)
                # Stash remaining budget for stages that care
                ctx.meta["stage_timeout_seconds"] = min(
                    remaining,
                    float(ctx.options.get("timeout_seconds") or remaining),
                )
                try:
                    result = stage.run(ctx, self.bus)
                except Exception as exc:
                    logger.exception("stage %s crashed", stage.name)
                    result = StageResult(
                        status=StageRunStatus.FAILED,
                        message=str(exc),
                        hard_fail=not stage.optional,
                    )

                ctx.record_stage(
                    js,
                    result.status,
                    message=result.message or result.skip_reason,
                    artifact_paths=list(result.artifact_paths),
                    started_at=stage_started,
                    finished_at=datetime.now(UTC),
                )
                level = (
                    EventLevel.ERROR if result.status is StageRunStatus.FAILED else EventLevel.INFO
                )
                self.bus.emit(
                    ctx.job_id,
                    result.message
                    or result.skip_reason
                    or f"Stage {stage.name} → {result.status.value}",
                    stage=js,
                    level=level,
                    progress=min(99, int(100 * (i + 1) / n)),
                    data={"status": result.status.value},
                )

                if result.hard_fail or (
                    result.status is StageRunStatus.FAILED and not stage.optional
                ):
                    ctx.hard_fail = True
                    ctx.error_code = f"{stage.name}_failed"
                    ctx.error_message = result.message or "stage failed"
                    # Still run finalize if registered after current
                    self._run_finalize_only(ctx, stages[i + 1 :])
                    self._finish(
                        ctx,
                        TerminalReason.FAILED,
                        JobStatus.FAILED,
                        ctx.error_message,
                    )
                    return ctx

            self._finish(ctx, TerminalReason.COMPLETED, JobStatus.COMPLETED, "completed")
        except Exception as exc:
            logger.exception("pipeline crashed")
            self._finish(ctx, TerminalReason.FAILED, JobStatus.FAILED, str(exc))
        return ctx

    def _run_finalize_only(self, ctx: JobContext, remaining: list[Stage]) -> None:
        for stage in remaining:
            if stage.name != "finalize":
                continue
            try:
                stage.run(ctx, self.bus)
            except Exception:
                logger.exception("finalize after hard-fail crashed")

    def _finish(
        self,
        ctx: JobContext,
        terminal: TerminalReason,
        status: JobStatus,
        message: str,
    ) -> None:
        ctx.status = status
        ctx.terminal = terminal
        ctx.progress = 100 if status is JobStatus.COMPLETED else ctx.progress
        ctx.touch()
        if ctx.manifest is not None:
            ctx.manifest.status = status
            ctx.manifest.updated_at = datetime.now(UTC)
        self.bus.emit(
            ctx.job_id,
            message,
            stage=JobStage.FINALIZE,
            level=EventLevel.INFO if status is JobStatus.COMPLETED else EventLevel.ERROR,
            progress=100 if status is JobStatus.COMPLETED else ctx.progress,
            terminal=terminal,
        )
        # Snapshot events for finalize / artifacts
        ctx.meta["event_history"] = [e.model_dump_json() for e in self.bus.history(ctx.job_id)]
        try:
            events_path = ctx.job_paths.events_log
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                "\n".join(ctx.meta["event_history"]) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        self.store.put(ctx)


def _env_flag(name: str) -> bool:
    import os

    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def build_default_registry() -> StageRegistry:
    """Register built-in stages (import side effects kept local)."""
    from auditor.pipeline.stages.compile import CompileStage
    from auditor.pipeline.stages.echidna import EchidnaStage
    from auditor.pipeline.stages.finalize import FinalizeStage
    from auditor.pipeline.stages.forge_fuzz import ForgeFuzzStage
    from auditor.pipeline.stages.llm_tests import LlmTestsStage
    from auditor.pipeline.stages.materialize import MaterializeStage
    from auditor.pipeline.stages.metamorphic import MetamorphicStage
    from auditor.pipeline.stages.mythril import MythrilStage
    from auditor.pipeline.stages.static_analysis import StaticAnalysisStage

    reg = StageRegistry()
    for stage in (
        MaterializeStage(),
        CompileStage(),
        StaticAnalysisStage(),
        MetamorphicStage(),
        LlmTestsStage(),
        ForgeFuzzStage(),
        EchidnaStage(),
        MythrilStage(),
        FinalizeStage(),
    ):
        reg.register(stage)
    return reg
