"""LLM-generated Foundry fuzz / invariant tests.

Single-model: one model plans+writes+repairs.
Dual-model (AUDIT_LLM_DUAL): strong **plan** model → test plan JSON;
cheaper **code** model writes/repairs `.t.sol`. Not consensus.
"""

from __future__ import annotations

from pathlib import Path

from auditor.contracts.enums import EventLevel, JobStage, StageRunStatus
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.llm.codegen import FOUNDRY_TEST_RULES, write_test_files
from auditor.pipeline.llm.plan_schema import (
    TestPlan,
    normalize_plan,
    plan_prompt_schema_hint,
)
from auditor.pipeline.profiles import STAGE_LLM_TESTS, AuditProfile
from auditor.pipeline.registry import StageResult
from auditor.security import SecurityConfig, run_command


class LlmTestsStage:
    name = STAGE_LLM_TESTS
    job_stage = JobStage.LLM_TESTS
    optional = True

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        if ctx.profile is AuditProfile.STATIC:
            return False, "static profile skips llm_tests"
        if not bool(ctx.options.get("enable_llm_tests", True)):
            return False, "enable_llm_tests=false"
        from auditor.pipeline.llm.client import llm_available

        if not llm_available():
            return False, "no LLM API key (static-only path)"
        if ctx.hard_fail:
            return False, "skipped after hard failure"
        return True, None

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        from auditor.pipeline.llm.client import (
            complete_text,
            dual_mode_enabled,
            model_for_role,
        )

        project = ctx.project_dir()
        sources = _collect_src(project)
        if not sources:
            return StageResult(status=StageRunStatus.SKIPPED, skip_reason="no src contracts")

        deep = ctx.profile is AuditProfile.DEEP
        art_dir = ctx.job_paths.resolve("artifacts/llm_tests")
        art_dir.mkdir(parents=True, exist_ok=True)

        # Reserve budget for later stages (fuzz + finalize)
        stage_budget = float(ctx.meta.get("stage_timeout_seconds") or 120)
        llm_budget = max(60.0, min(stage_budget * 0.7, 200.0))
        ctx.meta["llm_stage_budget"] = llm_budget

        dual = dual_mode_enabled()
        plan: TestPlan | None = None
        plan_text = ""

        if dual:
            plan_model = model_for_role("plan")
            code_model = model_for_role("code")
            bus.emit(
                ctx.job_id,
                f"Dual LLM: plan={plan_model} → code={code_model}",
                stage=JobStage.LLM_TESTS,
            )
            try:
                plan_text = complete_text(
                    _build_plan_prompt(sources, deep=deep),
                    max_tokens=2500,
                    role="plan",
                )
            except Exception as exc:
                return StageResult(
                    status=StageRunStatus.SKIPPED,
                    skip_reason=f"LLM plan error: {exc}",
                )
            (art_dir / "plan.raw.txt").write_text(plan_text, encoding="utf-8")
            plan = normalize_plan(plan_text, sources=sources)
            (art_dir / "plan.json").write_text(
                plan.model_dump_json(indent=2),
                encoding="utf-8",
            )
            for w in plan.parse_warnings:
                bus.emit(
                    ctx.job_id,
                    f"Plan warn: {w}",
                    stage=JobStage.LLM_TESTS,
                    level=EventLevel.WARN,
                )
            bus.emit(
                ctx.job_id,
                "Test plan ready; generating Foundry tests with code model"
                + (f" (needs_eth={plan.needs_eth})" if plan.needs_eth else ""),
                stage=JobStage.LLM_TESTS,
            )
            implement_prompt = _build_implement_prompt(
                sources, plan=plan, plan_raw=plan_text, deep=deep
            )
        else:
            bus.emit(
                ctx.job_id,
                f"Single LLM model={model_for_role('default')}",
                stage=JobStage.LLM_TESTS,
            )
            implement_prompt = _build_prompt(sources, deep=deep)

        force_eth = bool(plan.needs_eth) if plan is not None else _sources_suggest_eth(sources)
        max_attempts = 3
        last_err = ""
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt == 1:
                    reply = complete_text(
                        implement_prompt,
                        max_tokens=3500,
                        role="code" if dual else "default",
                    )
                else:
                    reply = complete_text(
                        implement_prompt
                        + f"\n\nPREVIOUS COMPILE/TEST ERRORS:\n{last_err[:6000]}\n"
                        + "Fix completely. Return full ### FILE blocks only.\n"
                        + "No markdown fences. No testFail*. "
                        + (
                            "Include receive() external payable if ETH is used.\n"
                            if force_eth
                            else ""
                        ),
                        max_tokens=3500,
                        role="repair" if dual else "default",
                    )
            except Exception as exc:
                return StageResult(
                    status=StageRunStatus.SKIPPED,
                    skip_reason=f"LLM error: {exc}",
                )

            written = write_test_files(project, reply, force_eth_receive=force_eth)
            if not written:
                last_err = "model returned no usable Solidity FILE blocks"
                bus.emit(
                    ctx.job_id,
                    f"No FILE blocks (attempt {attempt})",
                    stage=JobStage.LLM_TESTS,
                    level=EventLevel.WARN,
                )
                continue

            try:
                result = run_command(
                    ["forge", "build", "--skip", "script"],
                    timeout_seconds=min(90, llm_budget / max_attempts + 30),
                    config=SecurityConfig(
                        timeout_seconds=90,
                        memory_limit_bytes=None,
                        rlimit_cpu_seconds=None,
                    ),
                    cwd=project,
                )
            except Exception as exc:
                last_err = str(exc)
                continue

            if result.ok:
                gen_dir = art_dir / "generated"
                gen_dir.mkdir(parents=True, exist_ok=True)
                for rel in written:
                    src = project / rel
                    if src.is_file():
                        (gen_dir / Path(rel).name).write_text(
                            src.read_text(encoding="utf-8"), encoding="utf-8"
                        )
                bus.emit(
                    ctx.job_id,
                    f"LLM tests compiled ({len(written)} file(s), attempt {attempt})",
                    stage=JobStage.LLM_TESTS,
                )
                if deep:
                    _maybe_write_echidna_stub(project)
                paths = [f"artifacts/llm_tests/generated/{Path(w).name}" for w in written]
                if dual:
                    paths[:0] = [
                        "artifacts/llm_tests/plan.raw.txt",
                        "artifacts/llm_tests/plan.json",
                    ]
                return StageResult(
                    status=StageRunStatus.COMPLETED,
                    message=f"generated {len(written)} test file(s)"
                    + (" (dual plan→code)" if dual else ""),
                    artifact_paths=tuple(paths),
                )
            last_err = (result.stdout + result.stderr).decode("utf-8", errors="replace")
            bus.emit(
                ctx.job_id,
                f"LLM tests failed compile (attempt {attempt})",
                stage=JobStage.LLM_TESTS,
                level=EventLevel.WARN,
            )

        return StageResult(
            status=StageRunStatus.FAILED,
            message=f"LLM tests did not compile after {max_attempts} attempts",
            hard_fail=False,
        )


def _collect_src(project: Path) -> dict[str, str]:
    src = project / "src"
    out: dict[str, str] = {}
    if not src.is_dir():
        return out
    for path in sorted(src.rglob("*.sol")):
        rel = path.relative_to(project).as_posix()
        try:
            out[rel] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return out


def _sources_blob(sources: dict[str, str], *, limit: int = 35000) -> str:
    return "\n\n".join(f"// FILE: {k}\n{v}" for k, v in list(sources.items())[:8])[:limit]


def _sources_suggest_eth(sources: dict[str, str]) -> bool:
    blob = "\n".join(sources.values()).lower()
    return any(k in blob for k in ("payable", "msg.value", "withdraw", "deposit", ".call{value"))


def _build_plan_prompt(sources: dict[str, str], *, deep: bool) -> str:
    deep_note = "Include stateful invariant ideas when useful.\n" if deep else ""
    return (
        "Analyze the Solidity contracts and produce a **test plan** for Foundry.\n"
        "Do NOT write full Solidity test files.\n"
        f"{plan_prompt_schema_hint()}\n"
        f"{deep_note}\n"
        f"CONTRACTS:\n{_sources_blob(sources)}\n"
    )


def _build_implement_prompt(
    sources: dict[str, str],
    *,
    plan: TestPlan,
    plan_raw: str,
    deep: bool,
) -> str:
    deep_note = (
        "Include invariant-style tests using forge-std Test where the plan asks.\n" if deep else ""
    )
    return (
        "Implement Foundry tests from the PLAN checklist below.\n"
        "Use plan + source names only; do not invent unrelated fixtures.\n"
        f"{FOUNDRY_TEST_RULES}\n"
        "CHECKLIST (implement each item):\n"
        f"{plan.checklist_for_prompt()}\n\n"
        f"{deep_note}"
        "Return ONLY ### FILE / ### END blocks (no markdown fences).\n\n"
        f"PLAN_JSON:\n{plan.model_dump_json()[:8000]}\n\n"
        f"PLAN_RAW (if JSON incomplete):\n{plan_raw[:4000]}\n\n"
        f"CONTRACTS:\n{_sources_blob(sources)}\n"
    )


def _build_prompt(sources: dict[str, str], *, deep: bool) -> str:
    deep_note = "Also include invariant-style tests using forge-std Test.\n" if deep else ""
    return (
        "Write Foundry fuzz/unit tests for the Solidity contracts below.\n"
        "Target state-changing external functions from the sources only.\n"
        f"{FOUNDRY_TEST_RULES}\n"
        f"{deep_note}\n"
        f"CONTRACTS:\n{_sources_blob(sources)}\n"
    )


def _maybe_write_echidna_stub(project: Path) -> None:
    path = project / "test" / "InvariantsEchidna.sol"
    if path.exists():
        return
    path.write_text(
        "// SPDX-License-Identifier: MIT\n"
        "pragma solidity ^0.8.20;\n\n"
        "/// @dev Echidna property stub (deep profile).\n"
        "contract InvariantsEchidna {\n"
        "    bool private flag;\n"
        "    function setFlag(bool v) public { flag = v; }\n"
        "    function echidna_flag_false() public view returns (bool) {\n"
        "        return flag == false;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
