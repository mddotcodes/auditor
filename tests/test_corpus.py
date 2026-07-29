"""Curated corpus: compile + optional static family asserts (no LLM)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from auditor.contracts.enums import JobStatus
from auditor.contracts.jobs import AuditOptions, AuditRequest
from auditor.pipeline import AuditProfile, PipelineRunner, build_default_registry
from auditor.security.config import SecurityConfig

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "examples" / "corpus"

pytestmark = pytest.mark.integration


def _case_dirs() -> list[Path]:
    if not CORPUS.is_dir():
        return []
    return sorted(p for p in CORPUS.iterdir() if p.is_dir() and (p / "expected.json").is_file())


def _load_sources(case_dir: Path) -> dict[str, str]:
    src_root = case_dir / "src"
    sources: dict[str, str] = {}
    for path in sorted(src_root.rglob("*.sol")):
        rel = path.relative_to(case_dir).as_posix()
        # materialize expects paths like src/Foo.sol
        sources[rel] = path.read_text(encoding="utf-8")
    return sources


def _family_hit(findings: list[dict], families: list[str]) -> bool:
    if not families:
        return True
    needles = [f.lower() for f in families]
    for item in findings:
        blob = " ".join(
            [
                str(item.get("detector_id", "")),
                str(item.get("title", "")),
                str(item.get("tool", "")),
            ]
        ).lower()
        if any(n in blob for n in needles):
            return True
    return False


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


@pytest.mark.skipif(not _case_dirs(), reason="no corpus cases")
@pytest.mark.skipif(shutil.which("forge") is None, reason="forge not installed")
@pytest.mark.parametrize("case_dir", _case_dirs(), ids=lambda p: p.name)
def test_corpus_static_profile(runner: PipelineRunner, case_dir: Path) -> None:
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    sources = _load_sources(case_dir)
    assert sources, f"no sources in {case_dir}"

    req = AuditRequest(
        sources=sources,
        options=AuditOptions(enable_llm_tests=False, auto_fix_compile=False),
    )
    ctx = runner.submit(req, profile=AuditProfile.STATIC, run_inline=True)

    if expected.get("require_compile", True):
        # Compile hard-fail → FAILED; soft static failures may still COMPLETED
        compile_status = ctx.stage_results.get("compile")
        assert compile_status is not None
        from auditor.contracts.enums import StageRunStatus

        assert compile_status in {
            StageRunStatus.COMPLETED,
            StageRunStatus.FAILED,
        }
        if expected.get("require_compile") and compile_status is StageRunStatus.FAILED:
            pytest.fail(f"compile failed for {case_dir.name}: {ctx.error_message}")

    findings_path = ctx.job_paths.resolve("artifacts/static/findings.json")
    # finalize should write findings even on partial runs when completed
    if ctx.status is JobStatus.COMPLETED:
        assert ctx.job_paths.manifest.is_file()
        assert ctx.job_paths.resolve("artifacts/fingerprint.json").is_file()
        assert findings_path.is_file()
        doc = json.loads(findings_path.read_text(encoding="utf-8"))
        findings = doc.get("findings") or []
        min_f = int(expected.get("min_findings", 0))
        soft = bool(expected.get("soft_assert", False))
        if min_f > 0 and shutil.which("slither") is None:
            pytest.skip("slither not installed; skip finding family asserts")
        if min_f > 0:
            if soft:
                # soft: only check family if any findings exist
                if findings:
                    assert _family_hit(findings, expected.get("detector_families") or [])
            else:
                assert len(findings) >= min_f, (
                    f"{case_dir.name}: expected >= {min_f} findings, got {len(findings)}"
                )
                assert _family_hit(findings, expected.get("detector_families") or []), (
                    f"{case_dir.name}: no detector family match in {findings!r}"
                )


@pytest.mark.skipif(shutil.which("forge") is None, reason="forge not installed")
def test_vulnerable_bank_reentrancy_family(runner: PipelineRunner) -> None:
    """Baseline: examples/contracts/VulnerableBank should flag reentrancy when Slither works."""
    path = REPO / "examples" / "contracts" / "VulnerableBank.sol"
    req = AuditRequest(
        sources={"src/VulnerableBank.sol": path.read_text(encoding="utf-8")},
        options=AuditOptions(enable_llm_tests=False),
    )
    ctx = runner.submit(req, profile=AuditProfile.STATIC, run_inline=True)
    if ctx.status is not JobStatus.COMPLETED:
        pytest.skip(f"job not completed: {ctx.status} {ctx.error_message}")
    if shutil.which("slither") is None:
        pytest.skip("slither not installed")
    findings_path = ctx.job_paths.resolve("artifacts/static/findings.json")
    assert findings_path.is_file()
    findings = json.loads(findings_path.read_text(encoding="utf-8")).get("findings") or []
    assert len(findings) >= 1
    assert _family_hit(
        findings,
        ["reentrancy", "reentrancy-eth", "reentrancy-no-eth"],
    )
