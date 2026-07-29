"""Solidity pragma parsing and conflict policy."""

from __future__ import annotations

import pytest

from auditor.ingest.errors import PragmaConflictError
from auditor.ingest.pragma import (
    analyze_pragmas,
    parse_pragma_expression,
)


def test_caret_range() -> None:
    rng = parse_pragma_expression("^0.8.20")
    assert rng.describe() == ">=0.8.20 <0.9.0"


def test_compound_range() -> None:
    rng = parse_pragma_expression(">=0.7.0 <0.9.0")
    assert rng.min_v is not None
    assert rng.max_v is not None
    assert str(rng.min_v) == "0.7.0"
    assert str(rng.max_v) == "0.9.0"
    assert rng.max_inclusive is False


def test_exact_pin() -> None:
    rng = parse_pragma_expression("0.8.19")
    assert rng.min_v == rng.max_v
    assert str(rng.min_v) == "0.8.19"


def test_compatible_pragmas_choose_solc() -> None:
    sources = {
        "src/A.sol": "pragma solidity ^0.8.20;\ncontract A {}",
        "src/B.sol": "pragma solidity >=0.8.20 <0.9.0;\ncontract B {}",
    }
    info = analyze_pragmas(sources)
    assert info.solc_version == "0.8.28"
    assert info.version_range is not None
    assert "0.8.20" in info.version_range


def test_conflicting_major_rejected() -> None:
    sources = {
        "src/A.sol": "pragma solidity ^0.7.6;\ncontract A {}",
        "src/B.sol": "pragma solidity ^0.8.20;\ncontract B {}",
    }
    with pytest.raises(PragmaConflictError, match="conflicting"):
        analyze_pragmas(sources)


def test_no_pragma_leaves_solc_unset() -> None:
    info = analyze_pragmas({"src/A.sol": "contract A {}"})
    assert info.solc_version is None
    assert info.files_without_pragma == ("src/A.sol",)


def test_invalid_pragma_expression() -> None:
    with pytest.raises(PragmaConflictError):
        parse_pragma_expression("not-a-version")
