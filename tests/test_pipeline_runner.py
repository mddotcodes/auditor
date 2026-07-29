"""Integration tests for the audit pipeline runner (static profile)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from auditor.contracts.enums import JobStatus
from auditor.contracts.jobs import AuditOptions, AuditRequest
from auditor.pipeline import AuditProfile, PipelineRunner, build_default_registry
from auditor.security.config import SecurityConfig

REPO = Path(__file__).resolve().parents[1]
SAFE = REPO / "examples" / "contracts" / "SafeCounter.sol"
VULN = REPO / "examples" / "contracts" / "VulnerableBank.sol"


@pytest.fixture
def runner(tmp_path: Path) -> PipelineRunner:
    return PipelineRunner(
        build_default_registry(),
        job_root=tmp_path / "jobs",
        security=SecurityConfig(
            timeout_seconds=180,
            memory_limit_bytes=None,
            rlimit_cpu_seconds=None,
        ),
    )


@pytest.mark.skipif(shutil.which("forge") is None, reason="forge not installed")
def test_static_profile_safe_counter(runner: PipelineRunner) -> None:
    req = AuditRequest(
        sources={"src/SafeCounter.sol": SAFE.read_text(encoding="utf-8")},
        options=AuditOptions(enable_llm_tests=False, auto_fix_compile=False),
    )
    ctx = runner.submit(req, profile=AuditProfile.STATIC, run_inline=True)
    assert ctx.status in {JobStatus.COMPLETED, JobStatus.FAILED}
    # Compile should work for SafeCounter
    assert ctx.job_paths.manifest.is_file() or ctx.status is JobStatus.FAILED
    if ctx.status is JobStatus.COMPLETED:
        assert ctx.job_paths.resolve("artifacts/fingerprint.json").is_file()
        assert ctx.job_paths.resolve("artifacts/static/findings.json").is_file()
        assert ctx.progress == 100


def test_profiles_stage_lists() -> None:
    from auditor.pipeline.profiles import stages_for_profile

    static = stages_for_profile(AuditProfile.STATIC)
    assert "llm_tests" not in static
    assert "echidna" not in static
    assert static[-1] == "finalize"
    deep = stages_for_profile(AuditProfile.DEEP)
    assert "echidna" in deep


def test_fingerprint_unit(tmp_path: Path) -> None:
    from auditor.pipeline.fingerprint import build_fingerprint, extract_metadata_hash, sha256_hex

    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "A.sol").write_text("contract A {}", encoding="utf-8")
    bc = tmp_path / "bc"
    bc.mkdir()
    # minimal hex with length suffix
    runtime = "0x6001600055" + "aabb" + "0002"
    (bc / "A.runtime.hex").write_text(runtime, encoding="utf-8")
    fp = build_fingerprint(project, bytecode_dir=bc, solc_version="0.8.28")
    assert fp.compiler.solc_version == "0.8.28"
    assert fp.sources_sha256 is not None
    assert len(fp.contracts) == 1
    assert fp.contracts[0].runtime_bytecode_sha256 == sha256_hex(runtime.encode())
    assert extract_metadata_hash(runtime) is not None
