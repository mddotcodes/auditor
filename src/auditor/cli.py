"""Local CLI: run the audit pipeline or pre-flight metrics without HTTP.

Console entry point: ``auditor-cli`` (see ``[project.scripts]`` in pyproject.toml).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, TextIO

from auditor.contracts.events import JobEvent
from auditor.contracts.jobs import AuditOptions, AuditRequest
from auditor.ingest.paths import FOUNDRY_CONFIG_NAME, map_source_paths
from auditor.observability.exit_codes import (  # noqa: F401 — re-export for tests/scripts
    EXIT_CANCELLED,
    EXIT_JOB_FAILED,
    EXIT_OK,
    EXIT_TIMED_OUT,
    EXIT_USAGE,
    exit_code_for_status,
)
from auditor.observability.logging import bound_log_context, configure_logging
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.metrics import compute_metrics, metrics_to_dict
from auditor.pipeline.profiles import AuditProfile, profile_from_env
from auditor.pipeline.runner import PipelineRunner, build_default_registry
from auditor.security.config import SecurityConfig


def find_foundry_root(path: Path) -> Path | None:
    """Walk parents for ``foundry.toml``; return project root or ``None``."""
    current = path.resolve()
    if current.is_file():
        current = current.parent
    for parent in [current, *current.parents]:
        if (parent / FOUNDRY_CONFIG_NAME).is_file():
            return parent
        if parent.parent == parent:
            break
    return None


def _read_sol(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def collect_sources(paths: Sequence[str | Path]) -> dict[str, str]:
    """Build a sources map from ``.sol`` files and/or project directories.

    - Directory → all ``**/*.sol`` under it; keys are paths relative to that root.
    - Bare ``.sol`` file → ``src/<name>.sol``, or path relative to a discovered
      Foundry root (e.g. ``test/Foo.t.sol``) when under such a tree.
    - Flat basename-only maps are rewritten under ``src/`` via
      :func:`map_source_paths` (Foundry-style trees are preserved).
    """
    if not paths:
        msg = "at least one path is required"
        raise ValueError(msg)

    raw: dict[str, str] = {}

    for raw_path in paths:
        p = Path(raw_path)
        if not p.exists():
            msg = f"path does not exist: {raw_path}"
            raise FileNotFoundError(msg)

        if p.is_file():
            if p.suffix.lower() != ".sol":
                msg = f"not a .sol file: {raw_path}"
                raise ValueError(msg)
            root = find_foundry_root(p)
            if root is not None:
                key = p.resolve().relative_to(root.resolve()).as_posix()
            else:
                key = f"src/{p.name}"
            if key in raw:
                msg = f"duplicate source key {key!r} from {raw_path}"
                raise ValueError(msg)
            raw[key] = _read_sol(p)
            continue

        if not p.is_dir():
            msg = f"not a file or directory: {raw_path}"
            raise ValueError(msg)

        root = p.resolve()
        found: list[Path] = sorted(x for x in root.rglob("*.sol") if x.is_file())
        if not found:
            msg = f"no .sol files under directory: {raw_path}"
            raise ValueError(msg)

        for sol in found:
            key = sol.relative_to(root).as_posix()
            if key in raw:
                msg = f"duplicate source key {key!r} from {sol}"
                raise ValueError(msg)
            raw[key] = _read_sol(sol)

    if not raw:
        msg = "no Solidity sources collected"
        raise ValueError(msg)
    # Preserve Foundry layouts; place flat basename-only .sol sets under src/.
    return map_source_paths(raw)


def resolve_profile(cli_value: str | None) -> AuditProfile:
    """CLI ``--profile``, else ``AUDIT_PROFILE``, else ``static`` (local-friendly)."""
    if cli_value is not None:
        try:
            return AuditProfile(cli_value.strip().lower())
        except ValueError as exc:
            allowed = ", ".join(p.value for p in AuditProfile)
            msg = f"invalid profile {cli_value!r}; choose one of: {allowed}"
            raise ValueError(msg) from exc
    if "AUDIT_PROFILE" in os.environ:
        return profile_from_env()
    return AuditProfile.STATIC


def _format_event_text(event: JobEvent) -> str:
    stage = event.stage.value if event.stage is not None else "-"
    return f"[{stage}] {event.message}"


def _print_event(event: JobEvent, *, as_json: bool, out: TextIO) -> None:
    if as_json:
        out.write(event.model_dump_json() + "\n")
    else:
        out.write(_format_event_text(event) + "\n")
    out.flush()


def _final_status_dict(ctx: JobContext) -> dict[str, Any]:
    return {
        "job_id": ctx.job_id,
        "status": ctx.status.value,
        "progress": ctx.progress,
        "stage": ctx.stage.value if ctx.stage is not None else None,
        "error_code": ctx.error_code,
        "error_message": ctx.error_message,
        "terminal": ctx.terminal.value if ctx.terminal is not None else None,
        "job_root": str(ctx.job_paths.job_root),
        "manifest": str(ctx.job_paths.manifest) if ctx.job_paths.manifest.is_file() else None,
    }


def cmd_run(
    paths: Sequence[str],
    *,
    profile: str | None = None,
    job_root: Path | None = None,
    as_json: bool = False,
    no_llm: bool = False,
    timeout: int | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Collect sources, run pipeline inline, stream events. Returns process exit code."""
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr

    try:
        sources = collect_sources(paths)
        prof = resolve_profile(profile)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_USAGE

    options = AuditOptions(
        enable_llm_tests=not no_llm,
        auto_fix_compile=False,
        timeout_seconds=timeout,
    )
    request = AuditRequest(sources=sources, options=options)

    security = SecurityConfig.from_env()
    # Prefer a local writable job root when env still points at container default.
    default_root = Path(os.environ.get("AUDIT_JOB_ROOT", Path.cwd() / "work" / "jobs"))
    root = Path(job_root) if job_root is not None else default_root
    if timeout is not None:
        security = replace(security, timeout_seconds=timeout)

    bus = EventBus()
    runner = PipelineRunner(
        build_default_registry(),
        bus=bus,
        job_root=root,
        security=security,
    )

    # Create job without running so we can subscribe with a known job_id first.
    ctx = runner.submit(request, profile=prof, run_inline=False)

    def _on_event(event: JobEvent) -> None:
        _print_event(event, as_json=as_json, out=stdout)

    bus.subscribe(ctx.job_id, _on_event)
    with bound_log_context(job_id=ctx.job_id, profile=prof.value):
        try:
            if not as_json:
                print(
                    f"job_id={ctx.job_id} profile={prof.value} files={len(sources)}",
                    file=stdout,
                )
            runner.run(ctx)
        finally:
            bus.unsubscribe(ctx.job_id, _on_event)

    status_payload = _final_status_dict(ctx)
    # Always emit a single machine-readable result line for orchestrators when
    # --json is set; human mode keeps the familiar status line.
    if as_json:
        stdout.write(json.dumps(status_payload, indent=None) + "\n")
    else:
        print(
            f"status={status_payload['status']}"
            f" progress={status_payload['progress']}"
            f" job_id={status_payload['job_id']}",
            file=stdout,
        )
        if status_payload.get("error_message"):
            print(f"error: {status_payload['error_message']}", file=stderr)
        if status_payload.get("manifest"):
            print(f"manifest={status_payload['manifest']}", file=stdout)

    return exit_code_for_status(ctx.status)


def _format_metrics_human(data: dict[str, Any]) -> str:
    tools = data.get("tools_available") or {}
    tool_line = ", ".join(f"{name}={'yes' if ok else 'no'}" for name, ok in sorted(tools.items()))
    lines = [
        f"files:            {data.get('file_count', 0)}",
        f"loc_total:        {data.get('loc_total', 0)}",
        f"loc_src_only:     {data.get('loc_src_only', 0)}",
        f"approx_cyclomatic:{data.get('approx_cyclomatic', 0)}",
        f"approx_tokens:    {data.get('approx_tokens', 0)}",
        f"pragma_hint:      {data.get('pragma_hint') or '-'}",
        f"tools:            {tool_line or '-'}",
    ]
    return "\n".join(lines)


def cmd_metrics(
    paths: Sequence[str],
    *,
    as_json: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Compute pre-flight metrics and print human or JSON output."""
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr

    try:
        sources = collect_sources(paths)
        result = compute_metrics(sources=sources)
        data = metrics_to_dict(result)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_USAGE

    if as_json:
        stdout.write(json.dumps(data, indent=2) + "\n")
    else:
        stdout.write(_format_metrics_human(data) + "\n")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auditor-cli",
        description="Run the Auditor pipeline or pre-flight metrics without HTTP.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run an audit pipeline on local sources")
    run_p.add_argument(
        "paths",
        nargs="+",
        help=".sol file(s) and/or directories (collects **/*.sol)",
    )
    run_p.add_argument(
        "--profile",
        choices=[p.value for p in AuditProfile],
        default=None,
        help="Audit depth profile (default: AUDIT_PROFILE env or static)",
    )
    run_p.add_argument(
        "--job-root",
        type=Path,
        default=None,
        help="Directory for per-job workspaces (default: AUDIT_JOB_ROOT or ./work/jobs)",
    )
    run_p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Stream events as JSON lines; print final status JSON",
    )
    run_p.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM test generation (enable_llm_tests=false)",
    )
    run_p.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Job wall-clock timeout in seconds",
    )

    metrics_p = sub.add_parser("metrics", help="Pre-flight LOC / complexity / token estimate")
    metrics_p.add_argument(
        "paths",
        nargs="+",
        help=".sol file(s) and/or directories (collects **/*.sol)",
    )
    metrics_p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print metrics as JSON",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns process exit code (0 / 1 / 2 / 3 / 4)."""
    # JSON application logs when AUDIT_LOG_FORMAT=json (events still use --json).
    configure_logging()
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        # argparse uses 0 for --help and 2 for usage errors.
        code = exc.code
        if code is None:
            return EXIT_OK
        if isinstance(code, int):
            return EXIT_OK if code == 0 else EXIT_USAGE
        return EXIT_USAGE

    if args.command == "run":
        return cmd_run(
            args.paths,
            profile=args.profile,
            job_root=args.job_root,
            as_json=args.as_json,
            no_llm=args.no_llm,
            timeout=args.timeout,
        )
    if args.command == "metrics":
        return cmd_metrics(args.paths, as_json=args.as_json)

    print(f"error: unknown command {args.command!r}", file=sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
