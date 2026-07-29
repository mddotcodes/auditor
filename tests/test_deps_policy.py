"""Dependency policy and remapping unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from auditor.deps import (
    DependencyMode,
    DependencyPolicy,
    apply_default_vendor_libs,
    default_remappings,
    list_bundled_packs,
)
from auditor.deps.remappings import detect_required_packs
from auditor.deps.vendor import vendor_root


def test_strict_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "AUDIT_DEPENDENCY_MODE",
        "AUDIT_ALLOW_REMOTE_INSTALL",
        "AUDIT_AUTO_VENDOR",
        "AUDIT_AUTO_VENDOR_PACKS",
    ):
        monkeypatch.delenv(key, raising=False)
    policy = DependencyPolicy.from_env()
    assert policy.mode is DependencyMode.STRICT
    assert policy.allow_remote_install is False
    assert policy.auto_vendor is True


def test_strict_forces_remote_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_DEPENDENCY_MODE", "strict")
    monkeypatch.setenv("AUDIT_ALLOW_REMOTE_INSTALL", "true")
    policy = DependencyPolicy.from_env()
    assert policy.allow_remote_install is False
    with pytest.raises(PermissionError, match="Remote install"):
        policy.assert_remote_install_allowed("forge-std")


def test_permissive_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_DEPENDENCY_MODE", "permissive")
    with pytest.raises(ValueError, match="permissive"):
        DependencyPolicy.from_env()


def test_default_remappings_include_oz_and_forge_std() -> None:
    lines = default_remappings()
    assert any(line.startswith("forge-std/=") for line in lines)
    assert any("@openzeppelin/contracts/=" in line for line in lines)


def test_detect_required_packs() -> None:
    sources = {
        "src/T.sol": 'import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";\n'
    }
    assert "openzeppelin-contracts" in detect_required_packs(sources)


def test_vendor_root_and_packs() -> None:
    root = vendor_root()
    packs = list_bundled_packs(root)
    assert "forge-std" in packs
    assert "openzeppelin-contracts" in packs


def test_apply_vendor_libs_offline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "src").mkdir()
    result = apply_default_vendor_libs(
        project,
        policy=DependencyPolicy(mode=DependencyMode.STRICT, auto_vendor=True),
    )
    assert "forge-std" in result.installed or "forge-std" in result.skipped_existing
    assert "openzeppelin-contracts" in result.installed
    assert (project / "lib" / "forge-std" / "src").is_dir()
    assert (project / "lib" / "openzeppelin-contracts" / "contracts").is_dir()
    assert (project / "remappings.txt").is_file()
    toml = (project / "foundry.toml").read_text(encoding="utf-8")
    assert "remappings" in toml
    assert "@openzeppelin/contracts/" in toml

    # Second apply skips existing
    again = apply_default_vendor_libs(
        project,
        policy=DependencyPolicy(mode=DependencyMode.STRICT, auto_vendor=True),
    )
    assert "forge-std" in again.skipped_existing
    assert again.installed == []


def test_auto_vendor_false_skips_copy(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    result = apply_default_vendor_libs(
        project,
        policy=DependencyPolicy(mode=DependencyMode.STRICT, auto_vendor=False),
    )
    assert result.installed == []
    assert not (project / "lib").exists() or not any((project / "lib").iterdir())


def test_byo_lib_preserved(tmp_path: Path) -> None:
    project = tmp_path / "project"
    lib_custom = project / "lib" / "solmate"
    lib_custom.mkdir(parents=True)
    (lib_custom / "marker.txt").write_text("keep", encoding="utf-8")
    apply_default_vendor_libs(project)
    assert (lib_custom / "marker.txt").read_text(encoding="utf-8") == "keep"
