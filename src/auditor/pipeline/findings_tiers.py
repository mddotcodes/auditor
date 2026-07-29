"""Classify normalized findings into consumer-facing tiers."""

from __future__ import annotations

from enum import StrEnum

from auditor.pipeline.findings import Finding, FindingsDocument, FindingSeverity


class FindingTier(StrEnum):
    SECURITY = "security"
    QUALITY = "quality"
    INFORMATIONAL = "informational"


# Detector id / title substrings (lowercase) → security-relevant
_SECURITY_MARKERS: tuple[str, ...] = (
    "reentrancy",
    "arbitrary-send",
    "unchecked-lowlevel",
    "unchecked-low-level",
    "unchecked-send",
    "unchecked-transfer",
    "suicidal",
    "delegatecall",
    "controlled-delegatecall",
    "tx-origin",
    "tx_origin",
    "access-control",
    "unprotected",
    "locked-ether",
    "arbitrary-send-eth",
    "eth-send-unchecked",
    "reentrancy-state",
    "reentrancy-eth",
    "reentrancy-no-eth",
    "msg-value",
    "unauthorized",
)

_INFORMATIONAL_MARKERS: tuple[str, ...] = (
    "solc-version",
    "naming-convention",
    "pragma",
    "push-zero",
    "push0",
    "unspecific-solidity",
    "constable-states",
    "immutable-states",
    "dead-code",
    "unused",
    "similar-names",
    "assembly",
    "low-level-calls",  # often informational companion to real issue
    "large-numeric",
    "centralization",  # often expected for Ownable
    "state-variable-could",
    "state-change-without-event",
    "costly-loop",
)


def classify_finding(f: Finding) -> FindingTier:
    """Map one finding to security | quality | informational."""
    blob = f"{f.detector_id} {f.title} {f.description}".lower()
    sev = f.severity

    if any(m in blob for m in _SECURITY_MARKERS):
        # low-severity reentrancy-benign etc. still security family but may be quality
        if sev in {FindingSeverity.INFO} and "reentrancy-benign" in blob:
            return FindingTier.INFORMATIONAL
        if sev in {FindingSeverity.LOW} and not any(
            x in blob for x in ("reentrancy", "arbitrary-send", "unchecked", "suicidal")
        ):
            return FindingTier.QUALITY
        return FindingTier.SECURITY

    if sev in {FindingSeverity.CRITICAL, FindingSeverity.HIGH}:
        return FindingTier.SECURITY

    if sev is FindingSeverity.MEDIUM:
        # medium without security markers → quality (e.g. erc20-interface)
        return FindingTier.QUALITY

    if any(m in blob for m in _INFORMATIONAL_MARKERS) or sev in {
        FindingSeverity.INFO,
        FindingSeverity.LOW,
    }:
        return FindingTier.INFORMATIONAL

    return FindingTier.QUALITY


def attach_tiers(findings: list[Finding]) -> list[Finding]:
    """Copy findings with ``extra['tier']`` set."""
    out: list[Finding] = []
    for f in findings:
        tier = classify_finding(f)
        extra = dict(f.extra)
        extra["tier"] = tier.value
        out.append(f.model_copy(update={"extra": extra}))
    return out


def tier_summary(doc: FindingsDocument) -> dict[str, object]:
    """Counts and top security titles for pipeline meta / events."""
    by_tier: dict[str, int] = {
        FindingTier.SECURITY.value: 0,
        FindingTier.QUALITY.value: 0,
        FindingTier.INFORMATIONAL.value: 0,
    }
    security_titles: list[str] = []
    for f in doc.findings:
        tier = f.extra.get("tier") if f.extra else None
        if not tier:
            tier = classify_finding(f).value
        by_tier[str(tier)] = by_tier.get(str(tier), 0) + 1
        if tier == FindingTier.SECURITY.value:
            security_titles.append(f"{f.severity.value}: {f.title}"[:120])
    return {
        "by_tier": by_tier,
        "security_top": security_titles[:10],
        "total": len(doc.findings),
    }
