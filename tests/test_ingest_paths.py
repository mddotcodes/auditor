"""Path normalization and traversal rejection for source ingestion."""

from __future__ import annotations

from pathlib import Path

import pytest

from auditor.ingest.errors import PathTraversalError
from auditor.ingest.paths import (
    map_source_paths,
    normalize_source_path,
    safe_project_join,
    write_text_under_project,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/A.sol", "src/A.sol"),
        ("./src/A.sol", "src/A.sol"),
        ("src//A.sol", "src/A.sol"),
        ("src\\B.sol", "src/B.sol"),
        ("test/Foo.t.sol", "test/Foo.t.sol"),
    ],
)
def test_normalize_ok(raw: str, expected: str) -> None:
    assert normalize_source_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "../etc/passwd",
        "src/../../etc/passwd",
        "/etc/passwd",
        "C:/Windows/system.ini",
        "c:\\Windows\\system.ini",
        "//server/share/x.sol",
        "foo\x00bar.sol",
        "..",
        "src/../../../x",
        "CON",
        "src/NUL.sol",
        "~/.ssh/id_rsa",
    ],
)
def test_normalize_rejects_adversarial(raw: str) -> None:
    with pytest.raises(PathTraversalError):
        normalize_source_path(raw)


def test_map_flat_sol_under_src() -> None:
    mapped = map_source_paths({"A.sol": "pragma solidity ^0.8.20;", "B.sol": "//"})
    assert set(mapped) == {"src/A.sol", "src/B.sol"}


def test_map_preserves_foundry_layout() -> None:
    sources = {
        "src/A.sol": "x",
        "test/A.t.sol": "y",
        "script/Deploy.s.sol": "z",
    }
    assert map_source_paths(sources) == sources


def test_map_preserves_nested_without_src_prefix() -> None:
    # Not flat (has directory) and not Foundry roots — keep as given.
    sources = {"contracts/A.sol": "x"}
    assert map_source_paths(sources) == sources


def test_safe_join_and_write(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = write_text_under_project(project, "src/Hello.sol", "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    assert target == safe_project_join(project, "src/Hello.sol")


def test_write_refuses_symlink_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = project / "evil.sol"
    link.symlink_to(outside)
    with pytest.raises(PathTraversalError, match=r"escapes|symlink"):
        write_text_under_project(project, "evil.sol", "overwrite")


def test_duplicate_after_normalization() -> None:
    with pytest.raises(PathTraversalError, match="duplicate"):
        map_source_paths({"./A.sol": "1", "A.sol": "2"})
