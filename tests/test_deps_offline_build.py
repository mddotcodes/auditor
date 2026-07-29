"""Optional integration: forge build with vendored OZ (skipped if forge missing)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from auditor.deps import DependencyPolicy, apply_default_vendor_libs
from auditor.security import SecurityConfig, run_command

REPO = Path(__file__).resolve().parents[1]
OZ_TOKEN = REPO / "examples" / "contracts" / "OzToken.sol"


@pytest.mark.skipif(shutil.which("forge") is None, reason="forge not installed on host")
def test_oz_token_builds_offline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "OzToken.sol").write_text(OZ_TOKEN.read_text(encoding="utf-8"), encoding="utf-8")
    apply_default_vendor_libs(
        project,
        policy=DependencyPolicy.from_env(),
    )
    # Offline: no network needed if solc already available via foundry
    result = run_command(
        ["forge", "build", "--skip", "test"],
        timeout_seconds=120,
        config=SecurityConfig(
            timeout_seconds=120,
            memory_limit_bytes=None,
            rlimit_cpu_seconds=None,
        ),
        cwd=project,
    )
    assert result.ok, result.stderr.decode("utf-8", errors="replace")
