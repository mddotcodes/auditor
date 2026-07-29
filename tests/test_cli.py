"""Tests for auditor-cli (M5.3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from auditor.cli import (
    EXIT_JOB_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    build_parser,
    cmd_metrics,
    collect_sources,
    find_foundry_root,
    main,
    resolve_profile,
)
from auditor.contracts.enums import JobStatus
from auditor.pipeline.profiles import AuditProfile

REPO = Path(__file__).resolve().parents[1]
SAFE = REPO / "examples" / "contracts" / "SafeCounter.sol"


def test_build_parser_help() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as ei:
        parser.parse_args(["--help"])
    assert ei.value.code == 0


def test_main_help_exit_zero() -> None:
    assert main(["--help"]) == EXIT_OK


def test_main_run_help() -> None:
    assert main(["run", "--help"]) == EXIT_OK


def test_main_metrics_help() -> None:
    assert main(["metrics", "--help"]) == EXIT_OK


def test_main_missing_command() -> None:
    # argparse required subparser → usage error
    assert main([]) == EXIT_USAGE


def test_collect_sources_bare_file() -> None:
    sources = collect_sources([SAFE])
    assert set(sources) == {"src/SafeCounter.sol"}
    assert "contract SafeCounter" in sources["src/SafeCounter.sol"]


def test_collect_sources_directory(tmp_path: Path) -> None:
    (tmp_path / "A.sol").write_text("pragma solidity ^0.8.20;\ncontract A {}", encoding="utf-8")
    (tmp_path / "B.sol").write_text("pragma solidity ^0.8.20;\ncontract B {}", encoding="utf-8")
    sources = collect_sources([tmp_path])
    # Flat basenames → under src/
    assert set(sources) == {"src/A.sol", "src/B.sol"}


def test_collect_sources_foundry_tree(tmp_path: Path) -> None:
    (tmp_path / "foundry.toml").write_text("[profile.default]\n", encoding="utf-8")
    src = tmp_path / "src"
    test = tmp_path / "test"
    src.mkdir()
    test.mkdir()
    (src / "Token.sol").write_text("contract Token {}", encoding="utf-8")
    (test / "Token.t.sol").write_text("contract TokenTest {}", encoding="utf-8")
    sources = collect_sources([tmp_path])
    assert set(sources) == {"src/Token.sol", "test/Token.t.sol"}


def test_collect_sources_file_under_foundry(tmp_path: Path) -> None:
    (tmp_path / "foundry.toml").write_text("[profile.default]\n", encoding="utf-8")
    test = tmp_path / "test"
    test.mkdir()
    tfile = test / "Foo.t.sol"
    tfile.write_text("contract FooTest {}", encoding="utf-8")
    sources = collect_sources([tfile])
    assert set(sources) == {"test/Foo.t.sol"}


def test_find_foundry_root(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "foundry.toml").write_text("[profile.default]\n", encoding="utf-8")
    nested = project / "src" / "x"
    nested.mkdir(parents=True)
    assert find_foundry_root(nested) == project.resolve()
    elsewhere = tmp_path / "other" / "deep"
    elsewhere.mkdir(parents=True)
    assert find_foundry_root(elsewhere) is None


def test_collect_sources_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        collect_sources([tmp_path / "missing.sol"])


def test_collect_sources_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"no \.sol"):
        collect_sources([tmp_path])


def test_metrics_on_example_contract() -> None:
    from io import StringIO

    out = StringIO()
    code = cmd_metrics([str(SAFE)], as_json=False, out=out)
    assert code == EXIT_OK
    text = out.getvalue()
    assert "loc_total" in text
    assert "files:" in text


def test_metrics_json_on_example() -> None:
    from io import StringIO

    out = StringIO()
    code = cmd_metrics([str(SAFE)], as_json=True, out=out)
    assert code == EXIT_OK
    data = json.loads(out.getvalue())
    assert data["file_count"] == 1
    assert data["loc_total"] > 0
    assert "tools_available" in data
    assert data["pragma_hint"] is not None


def test_main_metrics_cli() -> None:
    # Integration through main(); no forge required.
    assert main(["metrics", str(SAFE), "--json"]) == EXIT_OK


def test_resolve_profile_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIT_PROFILE", raising=False)
    assert resolve_profile(None) is AuditProfile.STATIC
    assert resolve_profile("deep") is AuditProfile.DEEP
    monkeypatch.setenv("AUDIT_PROFILE", "default")
    assert resolve_profile(None) is AuditProfile.DEFAULT


def test_resolve_profile_invalid() -> None:
    with pytest.raises(ValueError, match="invalid profile"):
        resolve_profile("turbo")


def test_run_with_mocked_runner(tmp_path: Path) -> None:
    """Optional: run path without forge — mock PipelineRunner."""
    from auditor.contracts.layout import JobPaths
    from auditor.pipeline.context import JobContext
    from auditor.pipeline.profiles import AuditProfile
    from auditor.security.config import SecurityConfig

    sol = tmp_path / "C.sol"
    sol.write_text("pragma solidity ^0.8.20;\ncontract C {}", encoding="utf-8")

    job_root = tmp_path / "jobs"
    paths = JobPaths(job_root=job_root, job_id="test-job-1")
    paths.ensure_skeleton()
    fake_ctx = JobContext(
        job_id="test-job-1",
        job_paths=paths,
        profile=AuditProfile.STATIC,
        security=SecurityConfig(timeout_seconds=60, memory_limit_bytes=None),
        sources={"src/C.sol": sol.read_text(encoding="utf-8")},
    )
    fake_ctx.status = JobStatus.COMPLETED
    fake_ctx.progress = 100

    mock_runner = MagicMock()
    mock_runner.submit.return_value = fake_ctx
    mock_runner.run.return_value = fake_ctx
    mock_runner.bus = MagicMock()
    # Real EventBus behavior for subscribe/unsubscribe is nicer:
    from auditor.pipeline.events import EventBus

    bus = EventBus()
    mock_runner.bus = bus
    mock_runner.submit.return_value = fake_ctx

    def _run(ctx: JobContext) -> JobContext:
        bus.emit(ctx.job_id, "mocked complete", progress=100)
        ctx.status = JobStatus.COMPLETED
        ctx.progress = 100
        return ctx

    mock_runner.run.side_effect = _run

    with (
        patch("auditor.cli.PipelineRunner", return_value=mock_runner),
        patch("auditor.cli.build_default_registry", return_value=MagicMock()),
    ):
        code = main(
            [
                "run",
                str(sol),
                "--profile",
                "static",
                "--no-llm",
                "--job-root",
                str(job_root),
            ]
        )
    assert code == EXIT_OK
    mock_runner.submit.assert_called_once()
    mock_runner.run.assert_called_once()


def test_run_mocked_failure_exit_code(tmp_path: Path) -> None:
    from auditor.contracts.layout import JobPaths
    from auditor.pipeline.context import JobContext
    from auditor.pipeline.events import EventBus
    from auditor.pipeline.profiles import AuditProfile
    from auditor.security.config import SecurityConfig

    sol = tmp_path / "Bad.sol"
    sol.write_text("contract Bad {}", encoding="utf-8")
    job_root = tmp_path / "jobs"
    paths = JobPaths(job_root=job_root, job_id="fail-job")
    paths.ensure_skeleton()
    fake_ctx = JobContext(
        job_id="fail-job",
        job_paths=paths,
        profile=AuditProfile.STATIC,
        security=SecurityConfig(timeout_seconds=30, memory_limit_bytes=None),
    )
    fake_ctx.status = JobStatus.FAILED
    fake_ctx.error_message = "compile failed"

    mock_runner = MagicMock()
    bus = EventBus()
    mock_runner.bus = bus
    mock_runner.submit.return_value = fake_ctx
    mock_runner.run.return_value = fake_ctx

    with (
        patch("auditor.cli.PipelineRunner", return_value=mock_runner),
        patch("auditor.cli.build_default_registry", return_value=MagicMock()),
    ):
        code = main(["run", str(sol), "--job-root", str(job_root), "--no-llm"])
    assert code == EXIT_JOB_FAILED


def test_run_bad_path_usage() -> None:
    assert main(["run", "/nonexistent/path/Contract.sol"]) == EXIT_USAGE
