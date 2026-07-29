"""Finalize: findings aggregate, fingerprint, manifest."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime

from auditor.contracts.enums import ArtifactKind, JobStage, StageRunStatus
from auditor.contracts.layout import JOB_LAYOUT
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.findings import dedup_findings
from auditor.pipeline.fingerprint import build_fingerprint, sourcify_oriented_meta
from auditor.pipeline.profiles import STAGE_FINALIZE
from auditor.pipeline.registry import StageResult


class FinalizeStage:
    name = STAGE_FINALIZE
    job_stage = JobStage.FINALIZE
    optional = False

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        return True, None

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        from auditor.pipeline.findings_tiers import attach_tiers, tier_summary

        paths: list[str] = []
        # Dedupe + tier labels
        ctx.findings.findings = attach_tiers(dedup_findings(ctx.findings.findings))
        findings_path = ctx.job_paths.resolve(JOB_LAYOUT.findings)
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        findings_path.write_text(
            ctx.findings.model_dump_json(indent=2),
            encoding="utf-8",
        )
        paths.append(JOB_LAYOUT.findings)
        tiers = tier_summary(ctx.findings)
        by_tier = tiers.get("by_tier")
        if not isinstance(by_tier, dict):
            by_tier = {}
        bus.emit(
            ctx.job_id,
            (
                "Findings summary: "
                f"security={by_tier.get('security', 0)} "
                f"quality={by_tier.get('quality', 0)} "
                f"informational={by_tier.get('informational', 0)}"
            ),
            stage=JobStage.FINALIZE,
            data={"findings_tiers": tiers},
        )

        # Fingerprint
        bc_dir = ctx.job_paths.resolve(JOB_LAYOUT.bytecode_dir)
        solc = None
        if isinstance(ctx.meta.get("compile"), dict):
            solc = ctx.meta["compile"].get("solc")
        fp = build_fingerprint(
            ctx.project_dir(),
            bytecode_dir=bc_dir if bc_dir.is_dir() else None,
            solc_version=solc,
        )
        fp_path = ctx.job_paths.resolve(JOB_LAYOUT.fingerprint)
        fp_path.parent.mkdir(parents=True, exist_ok=True)
        fp_path.write_text(fp.model_dump_json(indent=2), encoding="utf-8")
        paths.append(JOB_LAYOUT.fingerprint)

        sourcify_meta = sourcify_oriented_meta(fp, ctx.project_dir())
        meta_path = ctx.job_paths.resolve(JOB_LAYOUT.pipeline_meta)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        pipeline_meta = {
            "job_id": ctx.job_id,
            "profile": ctx.profile.value,
            "status": ctx.status.value,
            "findings_by_severity": ctx.findings.counts_by_severity(),
            "findings_by_tool": ctx.findings.counts_by_tool(),
            "findings_by_tier": tiers.get("by_tier"),
            "security_top": tiers.get("security_top"),
            "forge_test": ctx.meta.get("forge_test"),
            "tools_run": ctx.findings.tools_run,
            "tools_failed": ctx.findings.tools_failed,
            "sourcify": sourcify_meta,
            "stage_results": {k: v.value for k, v in ctx.stage_results.items()},
        }
        meta_path.write_text(json.dumps(pipeline_meta, indent=2), encoding="utf-8")
        paths.append(JOB_LAYOUT.pipeline_meta)

        # Manifest
        manifest = ctx.ensure_manifest()
        manifest.status = ctx.status
        manifest.updated_at = datetime.now(UTC)
        manifest.fingerprint = fp
        # re-hash key artifacts into manifest
        for rel, kind in (
            (JOB_LAYOUT.findings, ArtifactKind.FINDINGS),
            (JOB_LAYOUT.fingerprint, ArtifactKind.FINGERPRINT),
            (JOB_LAYOUT.pipeline_meta, ArtifactKind.PIPELINE_META),
            (JOB_LAYOUT.forge_build_log, ArtifactKind.FORGE_BUILD_LOG),
            (JOB_LAYOUT.slither_raw, ArtifactKind.SLITHER_RAW),
        ):
            p = ctx.job_paths.resolve(rel)
            if p.is_file() and not any(a.path == rel for a in manifest.artifacts):
                with contextlib.suppress(OSError):
                    manifest.add_file(
                        p,
                        relative_path=rel,
                        kind=kind,
                        stage=JobStage.FINALIZE
                        if kind
                        in {
                            ArtifactKind.FINDINGS,
                            ArtifactKind.FINGERPRINT,
                            ArtifactKind.PIPELINE_META,
                        }
                        else None,
                        content_type="application/json" if rel.endswith(".json") else "text/plain",
                    )

        man_path = ctx.job_paths.manifest
        man_path.parent.mkdir(parents=True, exist_ok=True)
        man_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        paths.append(JOB_LAYOUT.manifest)

        # Durable event log if the runner attached history on ctx.meta
        events_path = ctx.job_paths.events_log
        events_path.parent.mkdir(parents=True, exist_ok=True)
        history = ctx.meta.get("event_history")
        if isinstance(history, list) and history:
            events_path.write_text(
                "\n".join(history) + "\n",
                encoding="utf-8",
            )
        elif not events_path.exists():
            events_path.write_text("", encoding="utf-8")

        bus.emit(
            ctx.job_id,
            "Finalize complete",
            stage=JobStage.FINALIZE,
            progress=100,
        )
        ctx.meta["finalize"] = pipeline_meta
        return StageResult(
            status=StageRunStatus.COMPLETED,
            message="manifest and fingerprint written",
            artifact_paths=tuple(paths),
        )
