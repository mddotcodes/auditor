"""End-to-end materialization of Solidity sources into a Foundry project."""

from __future__ import annotations

from pathlib import Path

import pytest

from auditor.contracts.layout import JobPaths
from auditor.ingest import (
    IngestLimits,
    InvalidFoundryConfigError,
    PathTraversalError,
    PayloadTooLargeError,
    PragmaConflictError,
    materialize_sources,
)


def _job(tmp_path: Path, job_id: str = "job1") -> JobPaths:
    paths = JobPaths(job_root=tmp_path, job_id=job_id)
    paths.ensure_skeleton()
    return paths


def test_happy_path_multi_file(tmp_path: Path) -> None:
    job = _job(tmp_path)
    sources = {
        "src/A.sol": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            "contract A { uint256 public x; }\n"
        ),
        "src/B.sol": ("pragma solidity ^0.8.20;\nimport {A} from ./A.sol;\ncontract B is A {}\n"),
        "test/A.t.sol": (
            "pragma solidity ^0.8.20;\nimport {A} from ../src/A.sol;\ncontract ATest {}\n"
        ),
    }
    result = materialize_sources(job, sources, apply_vendor_libs=False)

    assert result.project_dir == job.project
    assert "src/A.sol" in result.files_written
    assert "src/B.sol" in result.files_written
    assert "test/A.t.sol" in result.files_written
    assert "foundry.toml" in result.files_written
    assert result.pragma_info.solc_version == "0.8.28"
    assert result.total_bytes > 0
    assert result.total_lines >= 3

    assert (job.project / "src" / "A.sol").is_file()
    assert (job.project / "test").is_dir()
    assert (job.project / "lib").is_dir()
    assert (job.project / "script").is_dir()
    toml = (job.project / "foundry.toml").read_text(encoding="utf-8")
    assert 'solc_version = "0.8.28"' in toml
    assert "offline = true" in toml


def test_flat_sol_files_land_under_src(tmp_path: Path) -> None:
    job = _job(tmp_path)
    result = materialize_sources(
        job,
        {"Counter.sol": "pragma solidity ^0.8.20;\ncontract Counter {}"},
        apply_vendor_libs=False,
    )
    assert "src/Counter.sol" in result.files_written
    assert (job.project / "src" / "Counter.sol").is_file()


def test_path_traversal_rejected(tmp_path: Path) -> None:
    job = _job(tmp_path)
    with pytest.raises(PathTraversalError):
        materialize_sources(
            job,
            {"../escape.sol": "pragma solidity ^0.8.20;"},
            apply_vendor_libs=False,
        )
    with pytest.raises(PathTraversalError):
        materialize_sources(
            job,
            {"/etc/passwd": "x"},
            apply_vendor_libs=False,
        )


def test_oversize_total_rejected(tmp_path: Path) -> None:
    job = _job(tmp_path)
    limits = IngestLimits(
        max_source_bytes=100,
        max_source_files=50,
        max_file_bytes=10_000,
        max_loc=10_000,
    )
    with pytest.raises(PayloadTooLargeError, match="total source payload"):
        materialize_sources(
            job,
            {"src/A.sol": "x" * 200},
            limits=limits,
            apply_vendor_libs=False,
        )


def test_oversize_file_rejected(tmp_path: Path) -> None:
    job = _job(tmp_path)
    limits = IngestLimits(
        max_source_bytes=10_000,
        max_source_files=50,
        max_file_bytes=50,
        max_loc=10_000,
    )
    with pytest.raises(PayloadTooLargeError, match="per-file"):
        materialize_sources(
            job,
            {"src/A.sol": "y" * 100},
            limits=limits,
            apply_vendor_libs=False,
        )


def test_too_many_files_rejected(tmp_path: Path) -> None:
    job = _job(tmp_path)
    limits = IngestLimits(
        max_source_bytes=10_000_000,
        max_source_files=3,
        max_file_bytes=10_000,
        max_loc=100_000,
    )
    sources = {f"src/F{i}.sol": "contract X {}" for i in range(5)}
    with pytest.raises(PayloadTooLargeError, match="too many source files"):
        materialize_sources(job, sources, limits=limits, apply_vendor_libs=False)


def test_pragma_conflict_rejected(tmp_path: Path) -> None:
    job = _job(tmp_path)
    with pytest.raises(PragmaConflictError):
        materialize_sources(
            job,
            {
                "src/A.sol": "pragma solidity ^0.7.0;\ncontract A {}",
                "src/B.sol": "pragma solidity ^0.8.0;\ncontract B {}",
            },
            apply_vendor_libs=False,
        )


def test_user_foundry_toml_validated(tmp_path: Path) -> None:
    job = _job(tmp_path)
    with pytest.raises(InvalidFoundryConfigError):
        materialize_sources(
            job,
            {
                "src/A.sol": "pragma solidity ^0.8.20;\ncontract A {}",
                "foundry.toml": '[profile.default]\nsrc = "/etc"\n',
            },
            apply_vendor_libs=False,
        )


def test_user_foundry_toml_accepted(tmp_path: Path) -> None:
    job = _job(tmp_path)
    user_toml = '[profile.default]\nsrc = "src"\nlibs = ["lib"]\nsolc_version = "0.8.20"\n'
    result = materialize_sources(
        job,
        {
            "src/A.sol": "pragma solidity ^0.8.20;\ncontract A {}",
            "foundry.toml": user_toml,
        },
        apply_vendor_libs=False,
    )
    written = (job.project / "foundry.toml").read_text(encoding="utf-8")
    assert 'solc_version = "0.8.20"' in written
    assert result.foundry_toml == "foundry.toml"


def test_adversarial_filenames(tmp_path: Path) -> None:
    job = _job(tmp_path)
    cases = [
        {"..\\..\\windows.sol": "x"},
        {"src/../../x.sol": "x"},
        {"foo\x00.sol": "x"},
        {"": "x"},
    ]
    for sources in cases:
        with pytest.raises((PathTraversalError, PayloadTooLargeError)):
            materialize_sources(job, sources, apply_vendor_libs=False)


def test_empty_sources_rejected(tmp_path: Path) -> None:
    job = _job(tmp_path)
    with pytest.raises(PayloadTooLargeError):
        materialize_sources(job, {}, apply_vendor_libs=False)
