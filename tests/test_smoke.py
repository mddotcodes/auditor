"""Smoke tests to prove the toolchain and package import path work."""

from __future__ import annotations

from auditor import __version__


def test_version_is_semver_shaped() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_package_export() -> None:
    assert __version__ == "0.0.1"
