"""Normalize dual-LLM test plans (contract-agnostic)."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TestKind = Literal["fuzz", "unit", "invariant", "other"]


class PlanTarget(BaseModel):
    model_config = ConfigDict(extra="ignore")

    function: str = ""
    contract: str = ""
    kind: TestKind = "unit"
    goal: str = ""
    ideas: list[str] = Field(default_factory=list)
    suggested_test_name: str | None = None

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v: object) -> str:
        s = str(v or "unit").lower().strip()
        if s in {"fuzz", "unit", "invariant"}:
            return s
        return "other"


class TestPlan(BaseModel):
    """Machine plan produced by the planner model."""

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    contracts: list[dict[str, Any]] = Field(default_factory=list)
    targets: list[PlanTarget] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    needs_eth: bool = False
    needs_erc20: bool = False
    access_control: bool = False
    valid: bool = True
    parse_warnings: list[str] = Field(default_factory=list)

    def checklist_for_prompt(self) -> str:
        lines: list[str] = []
        if self.needs_eth:
            lines.append(
                "- needs_eth=true: Test/Attacker contracts that receive ETH MUST define "
                "`receive() external payable {}` (and fallback if used)."
            )
        if self.needs_erc20:
            lines.append("- needs_erc20=true: use deal/prank for token balances as appropriate.")
        if self.access_control:
            lines.append("- access_control: include unauthorized caller expectRevert tests.")
        for i, t in enumerate(self.targets, 1):
            name = t.suggested_test_name or _suggest_test_name(t)
            lines.append(
                f"- Target {i}: {t.contract}.{t.function or '*'} "
                f"[{t.kind}] goal={t.goal or 'n/a'} → implement test `{name}`"
            )
        for inv in self.invariants[:8]:
            lines.append(f"- Invariant: {inv}")
        return "\n".join(lines) if lines else "- Cover all state-changing external functions."


def _suggest_test_name(t: PlanTarget) -> str:
    fn = re.sub(r"[^A-Za-z0-9_]", "", t.function or "Call") or "Call"
    if t.kind == "fuzz":
        return f"testFuzz_{fn}"
    if t.kind == "invariant":
        return f"test_Invariant_{fn}"
    return f"test_{fn}"


def _infer_flags(data: dict[str, Any], sources_blob: str) -> tuple[bool, bool, bool]:
    blob = (json.dumps(data) + "\n" + sources_blob).lower()
    needs_eth = bool(
        data.get("needs_eth")
        or any(
            k in blob
            for k in (
                "msg.value",
                "payable",
                "withdraw",
                "deposit",
                "call{value",
                "ether",
                "eth ",
                "receive()",
            )
        )
    )
    needs_erc20 = bool(
        data.get("needs_erc20")
        or any(k in blob for k in ("erc20", "transferfrom", "balanceof", "approve(", "token"))
    )
    access = bool(
        data.get("access_control")
        or any(
            k in blob
            for k in ("onlyowner", "access control", "not owner", "unauthorized", "msg.sender ==")
        )
    )
    return needs_eth, needs_erc20, access


def normalize_plan(
    raw_text: str,
    *,
    sources: dict[str, str] | None = None,
) -> TestPlan:
    """Parse planner output into TestPlan; never raises on bad JSON."""
    warnings: list[str] = []
    data = _extract_json_object(raw_text)
    if data is None:
        warnings.append("plan JSON parse failed; using empty structured plan + raw text")
        data = {}
        valid = False
    else:
        valid = True

    sources_blob = ""
    if sources:
        sources_blob = "\n".join(sources.values())[:20000]

    needs_eth, needs_erc20, access = _infer_flags(data, sources_blob)
    # explicit booleans override inference if present
    if "needs_eth" in data:
        needs_eth = bool(data["needs_eth"])
    if "needs_erc20" in data:
        needs_erc20 = bool(data["needs_erc20"])
    if "access_control" in data:
        access = bool(data["access_control"])

    targets_raw = data.get("targets") or []
    targets: list[PlanTarget] = []
    if isinstance(targets_raw, list):
        for item in targets_raw:
            if isinstance(item, dict):
                try:
                    targets.append(PlanTarget.model_validate(item))
                except Exception:
                    warnings.append(f"skipped invalid target: {item!r}"[:120])

    notes = data.get("notes") or []
    if not isinstance(notes, list):
        notes = [str(notes)]
    invariants = data.get("invariants") or []
    if not isinstance(invariants, list):
        invariants = [str(invariants)]
    contracts = data.get("contracts") or []
    if not isinstance(contracts, list):
        contracts = []

    return TestPlan(
        summary=str(data.get("summary") or ""),
        contracts=[c for c in contracts if isinstance(c, dict)],
        targets=targets,
        invariants=[str(x) for x in invariants],
        notes=[str(x) for x in notes],
        needs_eth=needs_eth,
        needs_erc20=needs_erc20,
        access_control=access,
        valid=valid,
        parse_warnings=warnings,
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def plan_prompt_schema_hint() -> str:
    return (
        "Return a single JSON object (no markdown fences) with this shape:\n"
        "{\n"
        '  "summary": "one paragraph",\n'
        '  "needs_eth": true/false,\n'
        '  "needs_erc20": true/false,\n'
        '  "access_control": true/false,\n'
        '  "contracts": [{"name": "...", "path": "src/...", "risks": ["..."]}],\n'
        '  "targets": [\n'
        '    {"function": "withdraw", "contract": "MyContract",\n'
        '     "kind": "fuzz|unit|invariant", "goal": "...", "ideas": ["..."],\n'
        '     "suggested_test_name": "test_Withdraw"}\n'
        "  ],\n"
        '  "invariants": ["optional"],\n'
        '  "notes": ["..."]\n'
        "}\n"
        "Set needs_eth=true if any test must send/receive native ETH.\n"
    )
