"""Unit tests for Slither / Aderyn JSON parsers."""

from __future__ import annotations

from auditor.pipeline.findings import FindingConfidence, FindingSeverity
from auditor.pipeline.stages.static_parsers import (
    map_slither_confidence,
    map_slither_impact,
    parse_aderyn_json,
    parse_slither_json,
)

# Minimal Slither --json shape (wiki + real detector fields).
MINIMAL_SLITHER = {
    "success": True,
    "error": None,
    "results": {
        "detectors": [
            {
                "check": "reentrancy-eth",
                "impact": "High",
                "confidence": "Medium",
                "description": (
                    "Reentrancy in VulnerableBank.withdraw() "
                    "(VulnerableBank.sol#12-20):\n\tExternal calls:\n"
                ),
                "elements": [
                    {
                        "type": "function",
                        "name": "withdraw",
                        "source_mapping": {
                            "start": 100,
                            "length": 50,
                            "filename_relative": "src/VulnerableBank.sol",
                            "filename_short": "VulnerableBank.sol",
                            "lines": [12, 13, 14, 15, 16, 17, 18, 19, 20],
                            "starting_column": 5,
                            "ending_column": 6,
                        },
                    }
                ],
            },
            {
                "check": "solc-version",
                "impact": "Informational",
                "confidence": "High",
                "description": "Version constraint ^0.8.0 contains known severe issues",
                "elements": [],
            },
        ]
    },
}

# Minimal Aderyn report.json shape (Cyfrin highs-json-report.json).
MINIMAL_ADERYN = {
    "files_summary": {"total_source_units": 1, "total_sloc": 20},
    "issue_count": {"high": 1, "low": 1},
    "high_issues": {
        "issues": [
            {
                "title": "`delegatecall` to an Arbitrary Address",
                "description": (
                    "Making a `delegatecall` to an arbitrary address without any "
                    "checks is dangerous."
                ),
                "detector_name": "delegate-call-unchecked-address",
                "instances": [
                    {
                        "contract_path": "src/Bad.sol",
                        "line_no": 14,
                        "src": "391:15",
                    }
                ],
            }
        ]
    },
    "low_issues": {
        "issues": [
            {
                "title": "Centralization Risk for trusted owners",
                "description": "Contracts have owners with privileged rights.",
                "detector_name": "centralization-risk",
                "instances": [
                    {"contract_path": "src/Ownable.sol", "line_no": 8},
                    {"contract_path": "src/Ownable.sol", "line_no": 22},
                ],
            }
        ]
    },
    "detectors_used": ["delegate-call-unchecked-address", "centralization-risk"],
}


def test_map_slither_impact() -> None:
    assert map_slither_impact("High") is FindingSeverity.HIGH
    assert map_slither_impact("MEDIUM") is FindingSeverity.MEDIUM
    assert map_slither_impact("Informational") is FindingSeverity.INFO
    assert map_slither_impact("critical") is FindingSeverity.CRITICAL
    assert map_slither_impact(None) is FindingSeverity.UNKNOWN
    assert map_slither_impact("weird") is FindingSeverity.UNKNOWN


def test_map_slither_confidence() -> None:
    assert map_slither_confidence("High") is FindingConfidence.HIGH
    assert map_slither_confidence("low") is FindingConfidence.LOW
    assert map_slither_confidence("") is FindingConfidence.UNKNOWN


def test_parse_slither_json_minimal() -> None:
    findings = parse_slither_json(MINIMAL_SLITHER)
    assert len(findings) == 2

    reent = findings[0]
    assert reent.tool == "slither"
    assert reent.detector_id == "reentrancy-eth"
    assert reent.severity is FindingSeverity.HIGH
    assert reent.confidence is FindingConfidence.MEDIUM
    assert "Reentrancy" in reent.title
    assert reent.raw_ref == "artifacts/static/slither.json"
    assert len(reent.locations) == 1
    loc = reent.locations[0]
    assert loc.file == "src/VulnerableBank.sol"
    assert loc.start_line == 12
    assert loc.end_line == 20
    assert loc.start_col == 5

    info = findings[1]
    assert info.detector_id == "solc-version"
    assert info.severity is FindingSeverity.INFO
    assert info.confidence is FindingConfidence.HIGH
    assert info.locations == []


def test_parse_slither_bare_detectors() -> None:
    data = {
        "detectors": [
            {
                "check": "unused-return",
                "impact": "Medium",
                "confidence": "Low",
                "description": "Unused return",
                "elements": [],
            }
        ]
    }
    findings = parse_slither_json(data, raw_ref="custom.json")
    assert len(findings) == 1
    assert findings[0].severity is FindingSeverity.MEDIUM
    assert findings[0].raw_ref == "custom.json"


def test_parse_slither_empty_and_invalid() -> None:
    assert parse_slither_json(None) == []
    assert parse_slither_json({}) == []
    assert parse_slither_json({"success": True, "results": {}}) == []
    assert parse_slither_json("not a dict") == []


def test_parse_aderyn_json_minimal() -> None:
    findings = parse_aderyn_json(MINIMAL_ADERYN)
    assert len(findings) == 2

    high = next(f for f in findings if f.severity is FindingSeverity.HIGH)
    assert high.tool == "aderyn"
    assert high.detector_id == "delegate-call-unchecked-address"
    assert "delegatecall" in high.title
    assert high.raw_ref == "artifacts/static/aderyn.json"
    assert len(high.locations) == 1
    assert high.locations[0].file == "src/Bad.sol"
    assert high.locations[0].start_line == 14

    low = next(f for f in findings if f.severity is FindingSeverity.LOW)
    assert low.detector_id == "centralization-risk"
    assert len(low.locations) == 2
    assert low.locations[1].start_line == 22


def test_parse_aderyn_top_level_issues() -> None:
    data = {
        "issues": [
            {
                "title": "Something bad",
                "description": "Details",
                "detector_name": "custom-det",
                "severity": "medium",
                "instances": [{"file_path": "A.sol", "line": 3}],
            }
        ]
    }
    findings = parse_aderyn_json(data)
    assert len(findings) == 1
    assert findings[0].severity is FindingSeverity.MEDIUM
    assert findings[0].locations[0].file == "A.sol"
    assert findings[0].locations[0].start_line == 3


def test_parse_aderyn_empty() -> None:
    assert parse_aderyn_json(None) == []
    assert parse_aderyn_json({}) == []
    assert parse_aderyn_json({"high_issues": {"issues": []}}) == []
