"""Finding tier classification."""

from __future__ import annotations

from auditor.pipeline.findings import Finding, FindingsDocument, FindingSeverity
from auditor.pipeline.findings_tiers import FindingTier, classify_finding, tier_summary


def test_reentrancy_is_security() -> None:
    f = Finding(
        tool="slither",
        detector_id="reentrancy-eth",
        severity=FindingSeverity.HIGH,
        title="Reentrancy in withdraw",
    )
    assert classify_finding(f) is FindingTier.SECURITY


def test_pragma_is_informational() -> None:
    f = Finding(
        tool="aderyn",
        detector_id="unspecific-solidity-pragma",
        severity=FindingSeverity.LOW,
        title="Unspecific Solidity Pragma",
    )
    assert classify_finding(f) is FindingTier.INFORMATIONAL


def test_push_zero_informational() -> None:
    f = Finding(
        tool="aderyn",
        detector_id="push-zero-opcode",
        severity=FindingSeverity.LOW,
        title="PUSH0 Opcode",
    )
    assert classify_finding(f) is FindingTier.INFORMATIONAL


def test_arbitrary_send_security() -> None:
    f = Finding(
        tool="slither",
        detector_id="arbitrary-send-eth",
        severity=FindingSeverity.HIGH,
        title="arbitrary send",
    )
    assert classify_finding(f) is FindingTier.SECURITY


def test_tier_summary_counts() -> None:
    doc = FindingsDocument(
        findings=[
            Finding(
                tool="slither",
                detector_id="reentrancy-eth",
                severity=FindingSeverity.HIGH,
                title="r",
                extra={"tier": "security"},
            ),
            Finding(
                tool="aderyn",
                detector_id="push-zero-opcode",
                severity=FindingSeverity.LOW,
                title="p",
                extra={"tier": "informational"},
            ),
        ]
    )
    s = tier_summary(doc)
    assert s["by_tier"]["security"] == 1  # type: ignore[index]
    assert s["by_tier"]["informational"] == 1  # type: ignore[index]
