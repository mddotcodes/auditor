"""Unit tests for lightweight pre-flight metrics (M4.5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from auditor.pipeline.metrics import (
    MetricsResult,
    approx_cyclomatic,
    approx_tokens,
    compute_metrics,
    count_loc,
    metrics_to_dict,
    probe_tools,
    run_metrics_from_request,
)

# Sample with comments, control flow, and a pragma.
_SAMPLE_BANK = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice demo bank
contract SampleBank {
    mapping(address => uint256) public balances;

    function deposit() external payable {
        balances[msg.sender] += msg.value;
    }

    function withdraw() external {
        uint256 amount = balances[msg.sender];
        require(amount > 0, "no balance");
        if (amount > 1 ether) {
            // large withdrawal path
            revert("too large");
        } else {
            (bool ok, ) = msg.sender.call{value: amount}("");
            require(ok, "transfer failed");
        }
        balances[msg.sender] = 0;
    }
}
"""

_SAMPLE_LIB = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

library MathLib {
    function max(uint256 a, uint256 b) internal pure returns (uint256) {
        if (a >= b) {
            return a;
        }
        return b;
    }
}
"""

_SAMPLE_LOOP = """\
pragma solidity ^0.8.20;
contract Loops {
    function run(uint256 n) external pure returns (uint256 s) {
        for (uint256 i = 0; i < n; i++) {
            while (s < i) {
                s++;
            }
        }
        unchecked {
            s += 1;
        }
        assembly {
            s := add(s, 1)
        }
    }
}
"""


def test_count_loc_skips_empty_and_line_comments() -> None:
    src = "// header\n\npragma solidity ^0.8.20;\n  // indented comment\ncontract C {}\n\n"
    # pragma + contract only
    assert count_loc(src) == 2


def test_count_loc_keeps_inline_comment_code() -> None:
    assert count_loc("uint256 x = 1; // trail\n") == 1


def test_approx_cyclomatic_keywords() -> None:
    # require x2, if, else -> 4
    assert approx_cyclomatic(_SAMPLE_BANK) == 4


def test_approx_cyclomatic_loops_unchecked_assembly() -> None:
    # for, while, unchecked, assembly → 4
    assert approx_cyclomatic(_SAMPLE_LOOP) == 4


def test_approx_tokens_chars_over_four() -> None:
    assert approx_tokens("") == 0
    assert approx_tokens("abcd") == 1
    assert approx_tokens("abcde") == 2
    assert approx_tokens("a" * 8) == 2


def test_compute_metrics_from_sources() -> None:
    sources = {
        "src/SampleBank.sol": _SAMPLE_BANK,
        "lib/forge-std/src/MathLib.sol": _SAMPLE_LIB,
        "foundry.toml": "[profile.default]\n",  # ignored (not .sol)
    }
    m = compute_metrics(sources=sources)

    assert m.file_count == 2
    assert m.loc_total == count_loc(_SAMPLE_BANK) + count_loc(_SAMPLE_LIB)
    assert m.loc_src_only == count_loc(_SAMPLE_BANK)
    assert m.loc_src_only < m.loc_total
    assert m.approx_cyclomatic == approx_cyclomatic(_SAMPLE_BANK) + approx_cyclomatic(_SAMPLE_LIB)
    char_total = len(_SAMPLE_BANK) + len(_SAMPLE_LIB)
    assert m.approx_tokens == (char_total + 3) // 4
    assert m.pragma_hint == "0.8.20"
    assert set(m.tools_available) == {
        "forge",
        "slither",
        "aderyn",
        "echidna",
        "mythril",
    }
    assert all(isinstance(v, bool) for v in m.tools_available.values())


def test_compute_metrics_lib_exclusion_path_styles() -> None:
    sources = {
        "lib/oz/Token.sol": "pragma solidity ^0.8.0;\ncontract T {}\n",
        "./lib/other/X.sol": "pragma solidity ^0.8.0;\ncontract X {}\n",
        "src/App.sol": "pragma solidity ^0.8.0;\ncontract A {}\n",
    }
    m = compute_metrics(sources=sources)
    assert m.file_count == 3
    assert m.loc_src_only == count_loc(sources["src/App.sol"])
    assert m.loc_total == sum(count_loc(c) for c in sources.values())


def test_compute_metrics_pragma_conflict_hint() -> None:
    sources = {
        "src/A.sol": "pragma solidity ^0.7.6;\ncontract A {}\n",
        "src/B.sol": "pragma solidity ^0.8.20;\ncontract B {}\n",
    }
    m = compute_metrics(sources=sources)
    assert m.pragma_hint is not None
    assert m.pragma_hint.startswith("conflict:")


def test_compute_metrics_no_pragma() -> None:
    m = compute_metrics(sources={"src/A.sol": "contract A {}\n"})
    assert m.pragma_hint is None
    assert m.loc_total == 1


def test_compute_metrics_requires_input() -> None:
    with pytest.raises(ValueError, match="sources and/or project_dir"):
        compute_metrics()


def test_compute_metrics_from_project_dir(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "lib" / "dep").mkdir(parents=True)
    (tmp_path / "src" / "Main.sol").write_text(
        "pragma solidity ^0.8.20;\ncontract Main { function f() external { if (true) {} } }\n",
        encoding="utf-8",
    )
    (tmp_path / "lib" / "dep" / "Lib.sol").write_text(
        "pragma solidity ^0.8.20;\ncontract Lib {}\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# not sol\n", encoding="utf-8")

    m = compute_metrics(project_dir=tmp_path)
    assert m.file_count == 2
    assert m.loc_src_only == count_loc((tmp_path / "src" / "Main.sol").read_text())
    assert m.approx_cyclomatic >= 1
    assert m.pragma_hint == "0.8.20"


def test_compute_metrics_sources_override_project_dir(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Disk.sol").write_text(
        "pragma solidity ^0.8.20;\ncontract Disk {}\n",
        encoding="utf-8",
    )
    memory = {"src/Mem.sol": "pragma solidity ^0.8.19;\ncontract Mem {}\n"}
    m = compute_metrics(sources=memory, project_dir=tmp_path)
    assert m.file_count == 1
    assert m.pragma_hint == "0.8.19"


def test_probe_tools_uses_which() -> None:
    with patch("auditor.pipeline.metrics.shutil.which") as which:
        which.side_effect = lambda name: "/usr/bin/" + name if name == "forge" else None
        tools = probe_tools()
    assert tools["forge"] is True
    assert tools["slither"] is False
    assert tools["aderyn"] is False
    assert tools["echidna"] is False
    assert tools["mythril"] is False


def test_metrics_to_dict_and_request_helper() -> None:
    sources = {"src/C.sol": "pragma solidity 0.8.20;\ncontract C {}\n"}
    d = run_metrics_from_request(sources)
    assert isinstance(d, dict)
    assert d["file_count"] == 1
    assert d["pragma_hint"] == "0.8.20"
    assert "tools_available" in d
    assert set(d.keys()) == {
        "loc_total",
        "loc_src_only",
        "file_count",
        "approx_cyclomatic",
        "approx_tokens",
        "tools_available",
        "pragma_hint",
    }

    m = compute_metrics(sources=sources)
    assert metrics_to_dict(m) == d
    assert isinstance(m, MetricsResult)
