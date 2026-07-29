"""Unit tests for metamorphic / mutability heuristics (no Docker)."""

from __future__ import annotations

import json
from pathlib import Path

from auditor.contracts.enums import ArtifactKind, JobStage, StageRunStatus
from auditor.contracts.layout import JOB_LAYOUT, JobPaths
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.findings import FindingSeverity
from auditor.pipeline.profiles import STAGE_METAMORPHIC, AuditProfile
from auditor.pipeline.stages.metamorphic import (
    KNOWN_METAMORPHIC_INIT,
    MetamorphicStage,
    analyze_runtime_bytecode,
    analyze_source,
)
from auditor.security.config import SecurityConfig

SAFE_COUNTER = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Trivial counter — clean-ish baseline for offline demos.
contract SafeCounter {
    uint256 public count;
    address public owner;

    event Incremented(address indexed by, uint256 newCount);

    constructor() {
        owner = msg.sender;
    }

    function increment() external {
        unchecked {
            count += 1;
        }
        emit Incremented(msg.sender, count);
    }

    function reset() external {
        require(msg.sender == owner, "not owner");
        count = 0;
    }
}
"""

METAMORPHIC_FIXTURE = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract MetamorphicFactory {
    event Deployed(address addr);

    function deploy(bytes32 salt, bytes memory initCode) external returns (address addr) {
        assembly {
            addr := create2(0, add(initCode, 0x20), mload(initCode), salt)
        }
        emit Deployed(addr);
    }

    function kill(address target) external {
        // Redeployable after selfdestruct when combined with CREATE2 above.
        selfdestruct(payable(msg.sender));
        target;
    }
}
"""

DELEGATECALL_FACTORY = """\
pragma solidity ^0.8.20;

contract ProxyFactory {
    function deployClone(bytes32 salt) external returns (address) {
        // minimal factory-ish pattern
        address impl = address(this);
        bytes memory code = abi.encodePacked(type(ProxyFactory).creationCode, salt);
        address addr;
        assembly {
            addr := create2(0, add(code, 0x20), mload(code), salt)
        }
        return addr;
    }

    fallback() external payable {
        address impl = address(0x1234);
        assembly {
            let ok := delegatecall(gas(), impl, 0, calldatasize(), 0, 0)
        }
    }
}
"""

WEAK_MENTIONS = """\
pragma solidity ^0.8.20;
contract Watcher {
    function check(address a) external view returns (bytes32) {
        bytes32 h;
        assembly { h := extcodehash(a) }
        return h; // codehash of peer
    }
}
"""


def _ctx(tmp_path: Path) -> JobContext:
    paths = JobPaths(job_root=tmp_path, job_id="job-meta-1")
    paths.ensure_skeleton()
    return JobContext(
        job_id="job-meta-1",
        job_paths=paths,
        profile=AuditProfile.STATIC,
        security=SecurityConfig.from_env(),
    )


def test_safe_counter_no_critical_findings() -> None:
    hits = analyze_source("project/src/SafeCounter.sol", SAFE_COUNTER)
    criticalish = [
        h
        for h in hits
        if h.severity
        in {
            FindingSeverity.CRITICAL,
            FindingSeverity.HIGH,
            FindingSeverity.MEDIUM,
        }
    ]
    assert criticalish == []
    # Info/low only acceptable; prefer empty for clean fixture.
    assert all(h.severity in {FindingSeverity.INFO, FindingSeverity.LOW} for h in hits)


def test_selfdestruct_create2_flagged_high() -> None:
    hits = analyze_source("project/src/Meta.sol", METAMORPHIC_FIXTURE)
    ids = {h.detector_id for h in hits}
    assert "selfdestruct_with_factory" in ids
    high = [h for h in hits if h.severity is FindingSeverity.HIGH]
    assert high
    assert high[0].file == "project/src/Meta.sol"
    assert high[0].start_line is not None


def test_delegatecall_with_factory_medium() -> None:
    hits = analyze_source("project/src/ProxyFactory.sol", DELEGATECALL_FACTORY)
    ids = {h.detector_id for h in hits}
    assert "delegatecall_with_factory" in ids
    med = [h for h in hits if h.detector_id == "delegatecall_with_factory"]
    assert med[0].severity is FindingSeverity.MEDIUM


def test_weak_mentions_info_or_low() -> None:
    hits = analyze_source("project/src/Watcher.sol", WEAK_MENTIONS)
    assert hits
    assert all(h.severity in {FindingSeverity.INFO, FindingSeverity.LOW} for h in hits)
    assert any(h.detector_id == "extcodehash_mention" for h in hits)


def test_bytecode_selfdestruct_medium() -> None:
    # Minimal: STOP then SELFDESTRUCT (0x00 0xff) — 0xff is a real opcode, not PUSH data.
    hits = analyze_runtime_bytecode("artifacts/compile/bytecode/X.runtime.hex", "0x00ff")
    ids = {h.detector_id for h in hits}
    assert "bytecode_selfdestruct" in ids
    assert any(h.severity is FindingSeverity.MEDIUM for h in hits)


def test_bytecode_push_immediate_ff_not_selfdestruct() -> None:
    # PUSH1 0xff should not count as SELFDESTRUCT opcode.
    hits = analyze_runtime_bytecode("artifacts/compile/bytecode/Y.runtime.hex", "0x60ff00")
    assert not any(h.detector_id == "bytecode_selfdestruct" for h in hits)


def test_known_metamorphic_init_fingerprint() -> None:
    blob = "0x" + KNOWN_METAMORPHIC_INIT
    hits = analyze_runtime_bytecode("artifacts/compile/bytecode/M.creation.hex", blob)
    assert any(h.detector_id == "known_metamorphic_init_code" for h in hits)
    assert any(h.severity is FindingSeverity.HIGH for h in hits)


def test_stage_skipped_without_inputs(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    stage = MetamorphicStage()
    assert stage.name == STAGE_METAMORPHIC
    assert stage.job_stage is JobStage.METAMORPHIC
    assert stage.optional is True
    should, reason = stage.should_run(ctx)
    assert should is False
    assert reason is not None
    assert "no sources" in reason


def test_stage_writes_artifact_and_findings(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    src = ctx.project_dir() / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "Meta.sol").write_text(METAMORPHIC_FIXTURE, encoding="utf-8")

    bc = ctx.job_paths.resolve(JOB_LAYOUT.bytecode_dir)
    bc.mkdir(parents=True, exist_ok=True)
    (bc / "Meta.runtime.hex").write_text("0x00ff", encoding="utf-8")

    bus = EventBus()
    stage = MetamorphicStage()
    assert stage.should_run(ctx)[0] is True
    result = stage.run(ctx, bus)

    assert result.status is StageRunStatus.COMPLETED
    assert JOB_LAYOUT.metamorphic_raw in result.artifact_paths

    raw_path = ctx.job_paths.resolve(JOB_LAYOUT.metamorphic_raw)
    assert raw_path.is_file()
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    assert data["tool"] == "metamorphic"
    assert data["heuristic"] is True
    assert data["finding_count"] >= 1

    assert ctx.findings.findings
    assert "metamorphic" in ctx.findings.tools_run
    assert any(f.tool == "metamorphic" for f in ctx.findings.findings)
    assert any(f.detector_id == "selfdestruct_with_factory" for f in ctx.findings.findings)

    kinds = {a.kind for a in ctx.ensure_manifest().artifacts}
    assert ArtifactKind.METAMORPHIC_RAW in kinds


def test_stage_safe_counter_not_critical(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    src = ctx.project_dir() / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "SafeCounter.sol").write_text(SAFE_COUNTER, encoding="utf-8")

    result = MetamorphicStage().run(ctx, EventBus())
    assert result.status is StageRunStatus.COMPLETED
    criticalish = [
        f
        for f in ctx.findings.findings
        if f.severity
        in {
            FindingSeverity.CRITICAL,
            FindingSeverity.HIGH,
            FindingSeverity.MEDIUM,
        }
    ]
    assert criticalish == []


def test_create2_and_selfdestruct_bytecode_high() -> None:
    # CREATE2 (0xf5) then SELFDESTRUCT (0xff)
    hits = analyze_runtime_bytecode("bc.runtime.hex", "0xf5ff")
    ids = {h.detector_id for h in hits}
    assert "bytecode_create2_selfdestruct" in ids
    assert any(h.severity is FindingSeverity.HIGH for h in hits)
