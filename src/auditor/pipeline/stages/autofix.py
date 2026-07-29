"""Optional LLM compile auto-fix (max 3 attempts orchestrated by CompileStage)."""

from __future__ import annotations

import re
from pathlib import Path

from auditor.contracts.enums import EventLevel, JobStage
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus


def try_auto_fix_compile(ctx: JobContext, bus: EventBus, *, compiler_log: str) -> bool:
    """Ask LLM for a full-file fix; apply only under project/. Returns True if files changed."""
    from auditor.pipeline.llm.client import complete_text, llm_available

    if not llm_available():
        bus.emit(
            ctx.job_id,
            "Auto-fix skipped: no LLM API key",
            stage=JobStage.COMPILE,
            level=EventLevel.WARN,
        )
        return False

    project = ctx.project_dir()
    sol_files = sorted(project.joinpath("src").rglob("*.sol")) if (project / "src").is_dir() else []
    if not sol_files:
        sol_files = sorted(project.rglob("*.sol"))
    # Cap context
    snippets: list[str] = []
    for path in sol_files[:10]:
        rel = path.relative_to(project).as_posix()
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if len(body) > 20_000:
            body = body[:20_000] + "\n// ... truncated ..."
        snippets.append(f"// FILE: {rel}\n{body}")

    prompt = (
        "You are fixing Solidity compile errors for a Foundry project.\n"
        "Return ONLY a series of blocks in this exact format (no markdown fences):\n"
        "### FILE: relative/path.sol\n"
        "<full new file contents>\n"
        "### END\n"
        "You may only modify files under src/ or existing project sources. "
        "Do not invent network installs.\n\n"
        f"COMPILER OUTPUT:\n{compiler_log[:8000]}\n\n"
        f"SOURCES:\n{''.join(snippets)[:40000]}\n"
    )
    try:
        reply = complete_text(prompt, max_tokens=4000)
    except Exception as exc:
        bus.emit(
            ctx.job_id,
            f"Auto-fix LLM error: {exc}",
            stage=JobStage.COMPILE,
            level=EventLevel.WARN,
        )
        return False

    changed = _apply_file_blocks(project, reply)
    if changed:
        bus.emit(
            ctx.job_id,
            f"Auto-fix applied {changed} file(s)",
            stage=JobStage.COMPILE,
        )
        # snapshot
        snap = ctx.job_paths.resolve("artifacts/compile/autofix")
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "last_reply.txt").write_text(reply[:100_000], encoding="utf-8")
    return changed > 0


_FILE_BLOCK = re.compile(
    r"### FILE:\s*(\S+)\s*\n(.*?)### END",
    re.DOTALL | re.IGNORECASE,
)


def _apply_file_blocks(project: Path, reply: str) -> int:
    count = 0
    for match in _FILE_BLOCK.finditer(reply):
        rel = match.group(1).strip().strip("`")
        body = match.group(2)
        if body.startswith("\n"):
            body = body[1:]
        if not rel or ".." in rel.split("/") or rel.startswith("/"):
            continue
        # Only allow under src/, test/, script/
        if not (rel.startswith("src/") or rel.startswith("test/") or rel.startswith("script/")):
            continue
        dest = project / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # ensure still under project
        try:
            dest.resolve().relative_to(project.resolve())
        except ValueError:
            continue
        dest.write_text(body, encoding="utf-8")
        count += 1
    return count
