"""LLM-generated Foundry fuzz / invariant tests."""

from __future__ import annotations

import re
from pathlib import Path

from auditor.contracts.enums import EventLevel, JobStage, StageRunStatus
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
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
        from auditor.pipeline.llm.client import complete_text

        project = ctx.project_dir()
        sources = _collect_src(project)
        if not sources:
            return StageResult(status=StageRunStatus.SKIPPED, skip_reason="no src contracts")

        prompt = _build_prompt(sources, deep=ctx.profile is AuditProfile.DEEP)
        bus.emit(ctx.job_id, "Generating Foundry tests via LLM", stage=JobStage.LLM_TESTS)

        max_attempts = 3
        last_err = ""
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt == 1:
                    reply = complete_text(prompt, max_tokens=3500)
                else:
                    reply = complete_text(
                        prompt
                        + f"\n\nPREVIOUS COMPILE ERRORS:\n{last_err[:6000]}\n"
                        + "Fix the test file(s) completely.\n",
                        max_tokens=3500,
                    )
            except Exception as exc:
                return StageResult(
                    status=StageRunStatus.SKIPPED,
                    skip_reason=f"LLM error: {exc}",
                )

            written = _write_test_blocks(project, reply)
            if not written:
                last_err = "model returned no ### FILE blocks"
                continue

            # compile-check tests
            try:
                result = run_command(
                    ["forge", "build", "--skip", "script"],
                    timeout_seconds=90,
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
                gen_dir = ctx.job_paths.resolve("artifacts/llm_tests/generated")
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
                # deep: also emit echidna property stub if requested
                if ctx.profile is AuditProfile.DEEP:
                    _maybe_write_echidna_stub(project, sources)
                return StageResult(
                    status=StageRunStatus.COMPLETED,
                    message=f"generated {len(written)} test file(s)",
                    artifact_paths=tuple(
                        f"artifacts/llm_tests/generated/{Path(w).name}" for w in written
                    ),
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


def _build_prompt(sources: dict[str, str], *, deep: bool) -> str:
    joined = "\n\n".join(f"// FILE: {k}\n{v}" for k, v in list(sources.items())[:8])
    deep_note = "Also include invariant-style tests using forge-std Test.\n" if deep else ""
    return (
        "Write Foundry fuzz/unit tests for the Solidity contracts below.\n"
        "Use forge-std/Test.sol. Target state-changing external functions.\n"
        "Return ONLY blocks:\n"
        "### FILE: test/Something.t.sol\n"
        "<full file>\n"
        "### END\n"
        f"{deep_note}\n"
        f"CONTRACTS:\n{joined[:35000]}\n"
    )


_FILE_BLOCK = re.compile(
    r"### FILE:\s*(\S+)\s*\n(.*?)### END",
    re.DOTALL | re.IGNORECASE,
)


def _write_test_blocks(project: Path, reply: str) -> list[str]:
    written: list[str] = []
    for match in _FILE_BLOCK.finditer(reply):
        rel = match.group(1).strip().strip("`")
        body = match.group(2)
        if body.startswith("\n"):
            body = body[1:]
        if not rel.startswith("test/") or ".." in rel.split("/"):
            # force under test/
            name = Path(rel).name
            if not name.endswith(".sol"):
                name += ".sol"
            rel = f"test/{name}"
        dest = project / rel
        try:
            dest.resolve().relative_to(project.resolve())
        except ValueError:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        written.append(rel)
    return written


def _maybe_write_echidna_stub(project: Path, sources: dict[str, str]) -> None:
    """Write a minimal Echidna property contract stub for deep profile."""
    path = project / "test" / "InvariantsEchidna.sol"
    if path.exists():
        return
    # trivial property placeholder — user/LLM can expand
    path.write_text(
        "// SPDX-License-Identifier: MIT\n"
        "pragma solidity ^0.8.20;\n\n"
        "/// @dev Echidna property stub (deep profile). Expand with real invariants.\n"
        "contract InvariantsEchidna {\n"
        "    bool private flag;\n"
        "    function setFlag(bool v) public { flag = v; }\n"
        "    function echidna_flag_false() public view returns (bool) {\n"
        "        return flag == false;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
