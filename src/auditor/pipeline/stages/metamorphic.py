"""Metamorphic / mutability red-flag stage (M4.4b).

Cheap offline heuristics inspired by public a16z metamorphic-contract-detector
signals (SELFDESTRUCT, DELEGATECALL, CREATE2 / factory deploy patterns, known
metamorphic init-code fingerprints). Reimplemented here so the pipeline does
not hard-depend on that archived repo.

**False-positive posture:** this stage is heuristic only — not a proof of
safety or immutability. Missing flags does not mean code cannot morph; present
flags do not prove a contract is metamorphic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auditor.contracts.enums import ArtifactKind, JobStage, StageRunStatus
from auditor.contracts.layout import JOB_LAYOUT
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus
from auditor.pipeline.findings import (
    Finding,
    FindingConfidence,
    FindingLocation,
    FindingSeverity,
)
from auditor.pipeline.profiles import STAGE_METAMORPHIC
from auditor.pipeline.registry import StageResult

TOOL = "metamorphic"
RAW_REL = JOB_LAYOUT.metamorphic_raw

# Well-known 0age/a16z metamorphic init-code fingerprint (hex, no 0x).
KNOWN_METAMORPHIC_INIT = "5860208158601c335a63aaf10f428752fa158151803b80938091923cf3"

# EVM opcodes of interest (as ints).
_OP_CREATE2 = 0xF5
_OP_DELEGATECALL = 0xF4
_OP_SELFDESTRUCT = 0xFF
_OP_PUSH1 = 0x60
_OP_PUSH32 = 0x7F

_RE_SELFDESTRUCT = re.compile(r"\bselfdestruct\b", re.IGNORECASE)
_RE_DELEGATECALL = re.compile(r"\bdelegatecall\b", re.IGNORECASE)
_RE_CREATE2 = re.compile(r"\bcreate2\b", re.IGNORECASE)
_RE_SALT = re.compile(r"\bsalt\b", re.IGNORECASE)
_RE_NEW = re.compile(r"\bnew\b")
_RE_CREATION_CODE = re.compile(r"creationCode\b")
_RE_FACTORY = re.compile(
    r"\b(factory|deploy(?:er|ment)?|clone|minimal\s*proxy)\b",
    re.IGNORECASE,
)
_RE_CODEHASH = re.compile(r"\bcodehash\b", re.IGNORECASE)
_RE_EXTCODEHASH = re.compile(r"\bextcodehash\b", re.IGNORECASE)
_RE_MORPH = re.compile(r"\bmeta?morph\w*\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _Hit:
    detector_id: str
    severity: FindingSeverity
    confidence: FindingConfidence
    title: str
    description: str
    file: str | None = None
    start_line: int | None = None
    extra: dict[str, Any] | None = None


class MetamorphicStage:
    """Optional stage: scan sources + runtime bytecode for mutability red flags."""

    name = STAGE_METAMORPHIC
    job_stage = JobStage.METAMORPHIC
    optional = True

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        sources = _collect_source_files(ctx)
        bytecode = _collect_runtime_bytecode(ctx)
        if not sources and not bytecode:
            return False, "no sources and no runtime bytecode"
        return True, None

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        source_files = _collect_source_files(ctx)
        bytecode_files = _collect_runtime_bytecode(ctx)
        if not source_files and not bytecode_files:
            return StageResult(
                status=StageRunStatus.SKIPPED,
                message="no sources and no runtime bytecode",
                skip_reason="no sources and no runtime bytecode",
            )

        hits: list[_Hit] = []
        scanned_sources: list[str] = []
        scanned_bytecode: list[str] = []

        for rel, path in source_files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned_sources.append(rel)
            hits.extend(analyze_source(rel, text))

        for rel, path in bytecode_files:
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            scanned_bytecode.append(rel)
            hits.extend(analyze_runtime_bytecode(rel, raw))

        findings = [_hit_to_finding(h, raw_ref=RAW_REL) for h in hits]
        ctx.add_findings(findings)
        if "metamorphic" not in ctx.findings.tools_run:
            ctx.findings.tools_run.append("metamorphic")

        payload: dict[str, Any] = {
            "schema_version": "1",
            "tool": TOOL,
            "heuristic": True,
            "disclaimer": (
                "Heuristic mutability / metamorphic red-flag scan only. "
                "Not a proof of safety or immutability."
            ),
            "scanned": {
                "sources": scanned_sources,
                "runtime_bytecode": scanned_bytecode,
            },
            "finding_count": len(findings),
            "findings": [f.model_dump(mode="json") for f in findings],
        }

        out_path = ctx.job_paths.resolve(RAW_REL)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        manifest = ctx.ensure_manifest()
        manifest.add_file(
            out_path,
            relative_path=RAW_REL,
            kind=ArtifactKind.METAMORPHIC_RAW,
            stage=JobStage.METAMORPHIC,
            content_type="application/json",
            optional=True,
        )

        bus.emit(
            ctx.job_id,
            f"Metamorphic heuristics: {len(findings)} finding(s) "
            f"({len(scanned_sources)} sources, {len(scanned_bytecode)} bytecode)",
            stage=JobStage.METAMORPHIC,
            data={"finding_count": len(findings)},
        )

        ctx.meta["metamorphic"] = {
            "finding_count": len(findings),
            "sources_scanned": len(scanned_sources),
            "bytecode_scanned": len(scanned_bytecode),
        }

        return StageResult(
            status=StageRunStatus.COMPLETED,
            message=f"metamorphic heuristics complete ({len(findings)} findings)",
            artifact_paths=(RAW_REL,),
        )


def analyze_source(relative_path: str, source: str) -> list[_Hit]:
    """Heuristic scan of a single Solidity source string."""
    hits: list[_Hit] = []
    lines = source.splitlines()

    sd_line = _first_match_line(lines, _RE_SELFDESTRUCT)
    dc_line = _first_match_line(lines, _RE_DELEGATECALL)
    create2_line = _first_match_line(lines, _RE_CREATE2)
    has_create2 = create2_line is not None
    has_factory = (
        has_create2
        or _first_match_line(lines, _RE_CREATION_CODE) is not None
        or (
            _first_match_line(lines, _RE_SALT) is not None
            and _first_match_line(lines, _RE_NEW) is not None
        )
        or _first_match_line(lines, _RE_FACTORY) is not None
    )
    factory_line = (
        create2_line
        or _first_match_line(lines, _RE_CREATION_CODE)
        or _first_match_line(lines, _RE_SALT)
        or _first_match_line(lines, _RE_FACTORY)
    )

    if sd_line is not None and has_factory:
        hits.append(
            _Hit(
                detector_id="selfdestruct_with_factory",
                severity=FindingSeverity.HIGH,
                confidence=FindingConfidence.MEDIUM,
                title="selfdestruct combined with CREATE2/factory deploy pattern",
                description=(
                    "Source uses selfdestruct together with CREATE2 / salt / factory "
                    "deploy patterns. This combination is a classic metamorphic or "
                    "redeploy mutability signal (heuristic, not proof)."
                ),
                file=relative_path,
                start_line=sd_line,
                extra={"factory_line": factory_line},
            )
        )
    elif sd_line is not None:
        hits.append(
            _Hit(
                detector_id="selfdestruct",
                severity=FindingSeverity.MEDIUM,
                confidence=FindingConfidence.MEDIUM,
                title="selfdestruct present in source",
                description=(
                    "Contract source references selfdestruct. Code at an address can "
                    "be erased (and potentially redeployed if CREATE2 was used)."
                ),
                file=relative_path,
                start_line=sd_line,
            )
        )

    if dc_line is not None and has_factory:
        hits.append(
            _Hit(
                detector_id="delegatecall_with_factory",
                severity=FindingSeverity.MEDIUM,
                confidence=FindingConfidence.LOW,
                title="delegatecall combined with CREATE2/factory deploy pattern",
                description=(
                    "delegatecall can load foreign code (including selfdestruct), and "
                    "factory/CREATE2 patterns enable address reuse. Combined, this is "
                    "a mutability red flag (high false-positive rate)."
                ),
                file=relative_path,
                start_line=dc_line,
                extra={"factory_line": factory_line},
            )
        )
    elif dc_line is not None and sd_line is None:
        # Standalone delegatecall is very common (proxies/libs); keep as low only
        # when no stronger selfdestruct finding already covers the file.
        hits.append(
            _Hit(
                detector_id="delegatecall",
                severity=FindingSeverity.LOW,
                confidence=FindingConfidence.LOW,
                title="delegatecall present in source",
                description=(
                    "delegatecall can execute external code in this contract's "
                    "context. Alone this is not metamorphic, but it can enable "
                    "indirect self-destruction."
                ),
                file=relative_path,
                start_line=dc_line,
            )
        )

    # Weak keyword / pattern mentions (info/low).
    weak: list[tuple[str, int | None, str]] = []
    for det, regex, label in (
        ("codehash_mention", _RE_CODEHASH, "codehash"),
        ("extcodehash_mention", _RE_EXTCODEHASH, "EXTCODEHASH"),
        ("metamorphic_mention", _RE_MORPH, "metamorphic/morph terminology"),
    ):
        ln = _first_match_line(lines, regex)
        if ln is not None:
            weak.append((det, ln, label))

    if has_create2 and sd_line is None:
        # CREATE2 alone without selfdestruct — informational factory signal.
        weak.append(
            (
                "create2_deploy",
                create2_line,
                "CREATE2",
            )
        )

    for det, ln, label in weak:
        # Avoid double-counting when already high/medium on factory+destruct.
        if det == "create2_deploy" and any(
            h.detector_id == "selfdestruct_with_factory" for h in hits
        ):
            continue
        sev = FindingSeverity.INFO
        conf = FindingConfidence.LOW
        hits.append(
            _Hit(
                detector_id=det,
                severity=sev,
                confidence=conf,
                title=f"Possible mutability-related mention: {label}",
                description=(
                    f"Source mentions {label}. Weak heuristic signal only — "
                    "not evidence of metamorphic behavior by itself."
                ),
                file=relative_path,
                start_line=ln,
            )
        )

    return hits


def analyze_runtime_bytecode(relative_path: str, hex_blob: str) -> list[_Hit]:
    """Scan runtime (or creation) bytecode hex for SELFDESTRUCT / CREATE2 / fingerprints."""
    hits: list[_Hit] = []
    cleaned = _normalize_hex(hex_blob)
    if not cleaned:
        return hits

    lower = cleaned.lower()
    if KNOWN_METAMORPHIC_INIT in lower:
        hits.append(
            _Hit(
                detector_id="known_metamorphic_init_code",
                severity=FindingSeverity.HIGH,
                confidence=FindingConfidence.HIGH,
                title="Known metamorphic init-code fingerprint present",
                description=(
                    "Bytecode contains the well-known 0age/a16z metamorphic factory "
                    "init-code pattern. Strong indicator of intentional metamorphosis."
                ),
                file=relative_path,
                extra={"fingerprint": KNOWN_METAMORPHIC_INIT},
            )
        )

    opcodes = _opcodes_present(cleaned)
    if _OP_SELFDESTRUCT in opcodes:
        hits.append(
            _Hit(
                detector_id="bytecode_selfdestruct",
                severity=FindingSeverity.MEDIUM,
                confidence=FindingConfidence.MEDIUM,
                title="SELFDESTRUCT opcode (0xff) in bytecode",
                description=(
                    "Runtime/creation bytecode contains the SELFDESTRUCT opcode "
                    "outside PUSH immediates. Contract code may be erasable."
                ),
                file=relative_path,
                extra={"opcode": "0xff"},
            )
        )
    if _OP_CREATE2 in opcodes and _OP_SELFDESTRUCT in opcodes:
        hits.append(
            _Hit(
                detector_id="bytecode_create2_selfdestruct",
                severity=FindingSeverity.HIGH,
                confidence=FindingConfidence.MEDIUM,
                title="CREATE2 and SELFDESTRUCT both present in bytecode",
                description=(
                    "Bytecode contains both CREATE2 (0xf5) and SELFDESTRUCT (0xff). "
                    "Together these enable deterministic redeploy after destruction."
                ),
                file=relative_path,
                extra={"opcodes": ["0xf5", "0xff"]},
            )
        )
    elif _OP_CREATE2 in opcodes:
        hits.append(
            _Hit(
                detector_id="bytecode_create2",
                severity=FindingSeverity.INFO,
                confidence=FindingConfidence.LOW,
                title="CREATE2 opcode (0xf5) in bytecode",
                description=(
                    "Bytecode contains CREATE2. Legitimate for factories/wallets; "
                    "notable when paired with selfdestruct elsewhere."
                ),
                file=relative_path,
                extra={"opcode": "0xf5"},
            )
        )
    if _OP_DELEGATECALL in opcodes and _OP_SELFDESTRUCT not in opcodes:
        hits.append(
            _Hit(
                detector_id="bytecode_delegatecall",
                severity=FindingSeverity.LOW,
                confidence=FindingConfidence.LOW,
                title="DELEGATECALL opcode (0xf4) in bytecode",
                description=(
                    "Bytecode contains DELEGATECALL. Can load external code "
                    "(including selfdestruct) without containing 0xff locally."
                ),
                file=relative_path,
                extra={"opcode": "0xf4"},
            )
        )

    return hits


def _hit_to_finding(hit: _Hit, *, raw_ref: str) -> Finding:
    locations: list[FindingLocation] = []
    if hit.file is not None:
        locations.append(
            FindingLocation(file=hit.file, start_line=hit.start_line, end_line=hit.start_line)
        )
    return Finding(
        tool=TOOL,
        detector_id=hit.detector_id,
        severity=hit.severity,
        confidence=hit.confidence,
        title=hit.title,
        description=hit.description,
        locations=locations,
        raw_ref=raw_ref,
        extra=dict(hit.extra or {}),
    )


def _first_match_line(lines: list[str], pattern: re.Pattern[str]) -> int | None:
    for i, line in enumerate(lines, start=1):
        # Skip pure comment lines for slightly fewer FP on docs.
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        if pattern.search(line):
            return i
    return None


def _normalize_hex(blob: str) -> str:
    text = blob.strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    # Drop whitespace/newlines
    text = re.sub(r"\s+", "", text)
    if not text or len(text) % 2 != 0:
        return ""
    if any(c not in "0123456789abcdef" for c in text):
        return ""
    return text


def _opcodes_present(hex_no_prefix: str) -> set[int]:
    """Walk bytecode skipping PUSH immediates; return set of opcode bytes seen."""
    data = bytes.fromhex(hex_no_prefix)
    found: set[int] = set()
    i = 0
    n = len(data)
    while i < n:
        op = data[i]
        found.add(op)
        if _OP_PUSH1 <= op <= _OP_PUSH32:
            i += 1 + (op - _OP_PUSH1 + 1)
        else:
            i += 1
    return found


def _collect_source_files(ctx: JobContext) -> list[tuple[str, Path]]:
    """Return (relative_posix, path) for .sol under project/src."""
    src_dir = ctx.project_dir() / "src"
    out: list[tuple[str, Path]] = []
    if not src_dir.is_dir():
        return out
    for path in sorted(src_dir.rglob("*.sol")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(ctx.job_paths.base).as_posix()
        except ValueError:
            rel = path.as_posix()
        out.append((rel, path))
    return out


def _collect_runtime_bytecode(ctx: JobContext) -> list[tuple[str, Path]]:
    """Return runtime .hex artifacts under artifacts/compile/bytecode/."""
    bc_dir = ctx.job_paths.resolve(JOB_LAYOUT.bytecode_dir)
    out: list[tuple[str, Path]] = []
    if not bc_dir.is_dir():
        return out
    for path in sorted(bc_dir.glob("*.runtime.hex")):
        if not path.is_file():
            continue
        rel = f"{JOB_LAYOUT.bytecode_dir}/{path.name}"
        out.append((rel, path))
    # Also scan creation hex for known metamorphic init fingerprint only
    # (handled in analyze_runtime_bytecode which is generic).
    for path in sorted(bc_dir.glob("*.creation.hex")):
        if not path.is_file():
            continue
        rel = f"{JOB_LAYOUT.bytecode_dir}/{path.name}"
        out.append((rel, path))
    return out
