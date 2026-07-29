"""Materialize sources / gist into Foundry project."""

from __future__ import annotations

from auditor.contracts.enums import JobStage, StageRunStatus
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.profiles import STAGE_MATERIALIZE
from auditor.pipeline.registry import StageResult
from auditor.security.config import NetworkPolicy


class MaterializeStage:
    name = STAGE_MATERIALIZE
    job_stage = JobStage.MATERIALIZE
    optional = False

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        return True, None

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        from auditor.ingest import materialize_sources

        sources = dict(ctx.sources)
        if not sources and ctx.gist_url:
            from auditor.ingest import fetch_gist_sources

            dest = ctx.job_paths.resolve("artifacts/source/gist")
            dest.mkdir(parents=True, exist_ok=True)
            # Fetch requires ALLOW_FETCH; fail closed otherwise
            policy = ctx.security.network_policy
            if policy is not NetworkPolicy.ALLOW_FETCH:
                # Try DENY first path: only warm cache would work; force allow if env says
                import os

                if os.environ.get("AUDIT_NETWORK_POLICY", "").lower() != "allow_fetch":
                    return StageResult(
                        status=StageRunStatus.FAILED,
                        message=(
                            "gist_url provided but network policy is not allow_fetch "
                            f"(current={policy.value})"
                        ),
                        hard_fail=True,
                    )
            sources = fetch_gist_sources(
                ctx.gist_url,
                dest_dir=dest,
                network_policy=NetworkPolicy.ALLOW_FETCH,
            )
            ctx.sources = sources

        if not sources:
            return StageResult(
                status=StageRunStatus.FAILED,
                message="no sources to materialize",
                hard_fail=True,
            )

        result = materialize_sources(ctx.job_paths, sources, apply_vendor_libs=True)
        ctx.meta["pragma"] = {
            "solc_version": result.pragma_info.solc_version,
            "files": result.files_written,
        }
        ctx.meta["project_dir"] = str(result.project_dir)
        bus.emit(
            ctx.job_id,
            f"Materialized {len(result.files_written)} files "
            f"({result.total_bytes} bytes, {result.total_lines} LOC)",
            stage=JobStage.MATERIALIZE,
        )
        return StageResult(
            status=StageRunStatus.COMPLETED,
            message="materialized project",
            artifact_paths=("project",),
        )
