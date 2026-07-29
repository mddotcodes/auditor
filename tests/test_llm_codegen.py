"""LLM codegen extraction / normalization."""

from __future__ import annotations

from pathlib import Path

from auditor.pipeline.llm.codegen import (
    extract_file_blocks,
    normalize_test_source,
    strip_md_fences,
    write_test_files,
)
from auditor.pipeline.llm.plan_schema import normalize_plan


def test_strip_fences() -> None:
    raw = "```solidity\n// SPDX\npragma solidity ^0.8.20;\ncontract T {}\n```"
    out = strip_md_fences(raw)
    assert out.startswith("// SPDX")
    assert "```" not in out


def test_extract_file_blocks() -> None:
    reply = """
### FILE: test/Foo.t.sol
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
contract FooTest is Test {}
```
### END
"""
    blocks = extract_file_blocks(reply)
    assert len(blocks) == 1
    assert blocks[0][0] == "test/Foo.t.sol"
    assert "pragma solidity" in blocks[0][1]
    assert "```" not in blocks[0][1]


def test_fallback_whole_file() -> None:
    reply = "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\ncontract X is Test {}"
    blocks = extract_file_blocks(reply)
    assert len(blocks) == 1
    assert blocks[0][0] == "test/Generated.t.sol"


def test_inject_receive(tmp_path: Path) -> None:
    body = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
contract FooTest is Test {
    function testX() public {
        vm.deal(address(this), 1 ether);
    }
}
"""
    out = normalize_test_source(body, force_eth_receive=True)
    assert "receive() external payable" in out


def test_write_test_files(tmp_path: Path) -> None:
    reply = """
### FILE: test/Bar.t.sol
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
contract BarTest {}
### END
"""
    written = write_test_files(tmp_path, reply)
    assert written == ["test/Bar.t.sol"]
    assert (tmp_path / "test" / "Bar.t.sol").is_file()


def test_normalize_plan_needs_eth() -> None:
    raw = """
{
  "summary": "test",
  "needs_eth": true,
  "targets": [{"function": "withdraw", "contract": "V", "kind": "unit", "goal": "x"}]
}
"""
    plan = normalize_plan(raw)
    assert plan.valid
    assert plan.needs_eth is True
    assert "needs_eth=true" in plan.checklist_for_prompt()


def test_normalize_plan_invalid_json() -> None:
    plan = normalize_plan("not json at all")
    assert plan.valid is False
    assert plan.parse_warnings
