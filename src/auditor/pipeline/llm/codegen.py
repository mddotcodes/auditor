"""Extract and normalize LLM-generated Foundry test sources."""

from __future__ import annotations

import re
from pathlib import Path

_FILE_BLOCK = re.compile(
    r"###\s*FILE:\s*(\S+)\s*\n(.*?)(?:###\s*END|\Z)",
    re.DOTALL | re.IGNORECASE,
)

_FENCE_LINE = re.compile(r"^\s*```(?:solidity|sol|json)?\s*$", re.IGNORECASE)


def strip_md_fences(body: str) -> str:
    """Remove markdown fences and leading language tags from a file body."""
    text = body.replace("\r\n", "\n").strip()
    text = re.sub(r"^```(?:solidity|sol|json)?\s*\n?", "", text, count=1, flags=re.IGNORECASE)
    text = re.sub(r"\n```(?:solidity|sol|json)?\s*$", "", text, flags=re.IGNORECASE)
    lines = [ln for ln in text.split("\n") if not _FENCE_LINE.match(ln)]
    text = "\n".join(lines).strip()
    # last resort: drop any remaining fence markers
    text = text.replace("```solidity", "").replace("```sol", "").replace("```", "")
    text = text.strip()
    return text + ("\n" if text else "")


def extract_file_blocks(reply: str) -> list[tuple[str, str]]:
    """Return list of (relative_path, body) from model reply."""
    out: list[tuple[str, str]] = []
    for match in _FILE_BLOCK.finditer(reply):
        rel = match.group(1).strip().strip("`").strip('"').strip("'")
        body = strip_md_fences(match.group(2))
        if not body.strip():
            continue
        if not rel.startswith("test/") or ".." in rel.split("/"):
            name = Path(rel).name
            if not name.endswith(".sol"):
                name += ".sol"
            rel = f"test/{name}"
        out.append((rel, body if body.endswith("\n") else body + "\n"))
    if out:
        return out
    # Fallback: whole reply looks like a single Solidity file
    cleaned = strip_md_fences(reply)
    if "pragma solidity" in cleaned or "contract " in cleaned:
        return [("test/Generated.t.sol", cleaned if cleaned.endswith("\n") else cleaned + "\n")]
    return []


def needs_eth_receive(body: str) -> bool:
    b = body.lower()
    return any(
        x in b
        for x in (
            "{value:",
            "msg.value",
            ".deposit{",
            ".withdraw(",
            "deal(address",
            "vm.deal",
            "call{value",
        )
    )


def ensure_receive_payable(body: str) -> str:
    """Inject receive() into contracts that handle ETH but lack it (Test/Attacker style)."""
    if "receive()" in body or "fallback()" in body:
        return body
    if not needs_eth_receive(body):
        return body

    # Insert receive before the last closing brace of each contract that references ETH helpers
    # Heuristic: inject into every contract definition once if ETH is used anywhere in file.
    receive_block = "\n    receive() external payable {}\n"

    def inject(match: re.Match[str]) -> str:
        head = match.group(0)
        # skip interfaces
        if head.lstrip().startswith("interface "):
            return head
        return head  # placeholder — we do brace-based inject below

    del inject

    # Find contract ... {  ... last }
    pattern = re.compile(
        r"(contract\s+\w+[^{]*\{)",
        re.MULTILINE,
    )
    parts: list[str] = []
    last = 0
    for m in pattern.finditer(body):
        parts.append(body[last : m.end()])
        # find matching close is hard; inject after opening brace
        parts.append(receive_block)
        last = m.end()
    parts.append(body[last:])
    return "".join(parts)


def ban_test_fail_hint(body: str) -> str:
    """Rename obsolete testFail* functions to test_RevertIf_*."""

    def _repl(match: re.Match[str]) -> str:
        name = match.group(1)
        rest = name[8:] if name.startswith("testFail") else name
        if not rest:
            rest = "Condition"
        return f"function test_RevertIf_{rest}("

    return re.sub(r"\bfunction\s+(testFail[A-Za-z0-9_]*)\s*\(", _repl, body)


def normalize_test_source(body: str, *, force_eth_receive: bool = False) -> str:
    body = strip_md_fences(body)
    body = ban_test_fail_hint(body)
    if force_eth_receive or needs_eth_receive(body):
        body = ensure_receive_payable(body)
    return body


def write_test_files(
    project: Path,
    reply: str,
    *,
    force_eth_receive: bool = False,
) -> list[str]:
    """Write extracted tests under project; return relative paths written."""
    written: list[str] = []
    blocks = extract_file_blocks(reply)
    for rel, body in blocks:
        body = normalize_test_source(body, force_eth_receive=force_eth_receive)
        dest = project / rel
        try:
            dest.resolve().relative_to(project.resolve())
        except ValueError:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        written.append(rel)
    return written


FOUNDRY_TEST_RULES = """
Foundry test rules (must follow):
- import "forge-std/Test.sol"; inherit Test.
- NEVER use testFail* (removed). For reverts:
    vm.expectRevert(...);
    target.functionThatReverts();
- Prefer test_RevertIf_ / test_When_ / testFuzz_ naming.
- Use deal/hoax/prank from forge-std when needed.
- If any test sends or receives native ETH, every Test and Attacker contract that
  may receive ETH MUST include: receive() external payable {}
- Return ONLY ### FILE blocks. No markdown fences (no ```). No prose outside blocks.
- First line of each file must be // SPDX or pragma — never ```solidity.
"""
