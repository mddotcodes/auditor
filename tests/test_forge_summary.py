"""Forge test JSON summary."""

from __future__ import annotations

from auditor.pipeline.forge_summary import summarize_forge_json


def test_summarize_pass_fail() -> None:
    parsed = {
        "test/X.t.sol:T": {
            "test_results": {
                "testA()": {"status": "Success"},
                "testB()": {"status": "Failure", "reason": "send failed"},
            }
        }
    }
    s = summarize_forge_json(parsed)
    assert s["passed"] == 1
    assert s["failed"] == 1
    assert s["ok"] is False
    assert "testB()" in s["failed_names"][0]
