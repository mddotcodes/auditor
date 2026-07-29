"""Pure parsers: Slither / Aderyn raw JSON → normalized Finding list."""

from __future__ import annotations

from typing import Any

from auditor.pipeline.findings import (
    Finding,
    FindingConfidence,
    FindingLocation,
    FindingSeverity,
)

# ---------------------------------------------------------------------------
# Severity / confidence mapping
# ---------------------------------------------------------------------------

_SLITHER_IMPACT: dict[str, FindingSeverity] = {
    "critical": FindingSeverity.CRITICAL,
    "high": FindingSeverity.HIGH,
    "medium": FindingSeverity.MEDIUM,
    "low": FindingSeverity.LOW,
    "informational": FindingSeverity.INFO,
    "info": FindingSeverity.INFO,
    "optimization": FindingSeverity.INFO,
}

_SLITHER_CONFIDENCE: dict[str, FindingConfidence] = {
    "high": FindingConfidence.HIGH,
    "medium": FindingConfidence.MEDIUM,
    "low": FindingConfidence.LOW,
}

# Aderyn buckets are severity tiers (high_issues / low_issues / …).
_ADERYN_BUCKET_SEVERITY: dict[str, FindingSeverity] = {
    "critical_issues": FindingSeverity.CRITICAL,
    "critical": FindingSeverity.CRITICAL,
    "high_issues": FindingSeverity.HIGH,
    "high": FindingSeverity.HIGH,
    "medium_issues": FindingSeverity.MEDIUM,
    "medium": FindingSeverity.MEDIUM,
    "low_issues": FindingSeverity.LOW,
    "low": FindingSeverity.LOW,
    "nc_issues": FindingSeverity.INFO,
    "informational_issues": FindingSeverity.INFO,
    "info_issues": FindingSeverity.INFO,
    "gas_issues": FindingSeverity.INFO,
}


def map_slither_impact(impact: str | None) -> FindingSeverity:
    if not impact or not str(impact).strip():
        return FindingSeverity.UNKNOWN
    return _SLITHER_IMPACT.get(str(impact).strip().lower(), FindingSeverity.UNKNOWN)


def map_slither_confidence(confidence: str | None) -> FindingConfidence:
    if not confidence or not str(confidence).strip():
        return FindingConfidence.UNKNOWN
    return _SLITHER_CONFIDENCE.get(str(confidence).strip().lower(), FindingConfidence.UNKNOWN)


def _as_dict(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        return data
    return None


def _first_line(text: str, *, limit: int = 200) -> str:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if len(line) > limit:
        return line[: limit - 1] + "…"
    return line


def _slither_locations(elements: Any) -> list[FindingLocation]:
    out: list[FindingLocation] = []
    if not isinstance(elements, list):
        return out
    for el in elements:
        if not isinstance(el, dict):
            continue
        sm = el.get("source_mapping")
        if not isinstance(sm, dict):
            continue
        filename = (
            sm.get("filename_relative")
            or sm.get("filename_short")
            or sm.get("filename_used")
            or sm.get("filename_absolute")
        )
        lines = sm.get("lines") or []
        start_line: int | None = None
        end_line: int | None = None
        if isinstance(lines, list) and lines:
            ints = [int(x) for x in lines if isinstance(x, int) or str(x).isdigit()]
            if ints:
                start_line = min(ints)
                end_line = max(ints)
        start_col = sm.get("starting_column")
        end_col = sm.get("ending_column")
        # Slither columns are 1-based; our schema allows 0+. Keep as-is when int.
        sc = int(start_col) if isinstance(start_col, int) else None
        ec = int(end_col) if isinstance(end_col, int) else None
        # FindingLocation forbids start_line < 1; drop invalid line numbers.
        if start_line is not None and start_line < 1:
            start_line = None
        if end_line is not None and end_line < 1:
            end_line = None
        if filename is None and start_line is None:
            continue
        out.append(
            FindingLocation(
                file=str(filename) if filename is not None else None,
                start_line=start_line,
                end_line=end_line,
                start_col=sc if sc is not None and sc >= 0 else None,
                end_col=ec if ec is not None and ec >= 0 else None,
            )
        )
    return out


def parse_slither_json(
    data: Any,
    *,
    raw_ref: str | None = "artifacts/static/slither.json",
) -> list[Finding]:
    """Parse Slither ``--json`` document into findings.

    Expected shape::

        {"success": true, "error": null, "results": {"detectors": [...]}}

    Also accepts a bare ``{"detectors": [...]}`` or a top-level detectors list.
    """
    root = _as_dict(data)
    if root is None:
        return []

    detectors: list[Any]
    results = root.get("results")
    if isinstance(results, dict) and isinstance(results.get("detectors"), list):
        detectors = results["detectors"]
    elif isinstance(root.get("detectors"), list):
        detectors = root["detectors"]
    elif isinstance(data, list):
        detectors = data
    else:
        return []

    findings: list[Finding] = []
    for det in detectors:
        if not isinstance(det, dict):
            continue
        check = str(det.get("check") or det.get("id") or "").strip()
        description = str(det.get("description") or "").strip()
        if not check and not description:
            continue
        detector_id = check or "unknown"
        title = _first_line(description) if description else detector_id
        if not title:
            title = detector_id
        locations = _slither_locations(det.get("elements"))
        extra: dict[str, Any] = {}
        if det.get("additional_fields") is not None:
            extra["additional_fields"] = det["additional_fields"]
        findings.append(
            Finding(
                tool="slither",
                detector_id=detector_id,
                severity=map_slither_impact(
                    str(det["impact"]) if det.get("impact") is not None else None
                ),
                confidence=map_slither_confidence(
                    str(det["confidence"]) if det.get("confidence") is not None else None
                ),
                title=title,
                description=description,
                locations=locations,
                raw_ref=raw_ref,
                extra=extra,
            )
        )
    return findings


def _aderyn_instances(instances: Any) -> list[FindingLocation]:
    out: list[FindingLocation] = []
    if not isinstance(instances, list):
        return out
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        path = (
            inst.get("contract_path")
            or inst.get("file_path")
            or inst.get("file")
            or inst.get("path")
        )
        line = inst.get("line_no")
        if line is None:
            line = inst.get("line")
        start_line: int | None = None
        if isinstance(line, int) and line >= 1:
            start_line = line
        elif isinstance(line, str) and line.isdigit() and int(line) >= 1:
            start_line = int(line)
        end_line = start_line
        # Optional end line
        el = inst.get("end_line_no") or inst.get("end_line")
        if isinstance(el, int) and el >= 1:
            end_line = el
        if path is None and start_line is None:
            continue
        out.append(
            FindingLocation(
                file=str(path) if path is not None else None,
                start_line=start_line,
                end_line=end_line,
            )
        )
    return out


def _parse_aderyn_issue(
    issue: dict[str, Any],
    *,
    severity: FindingSeverity,
    raw_ref: str | None,
) -> Finding | None:
    detector_id = str(
        issue.get("detector_name") or issue.get("detector") or issue.get("id") or ""
    ).strip()
    title = str(issue.get("title") or "").strip()
    description = str(issue.get("description") or "").strip()
    if not detector_id and not title:
        return None
    if not detector_id:
        detector_id = title[:80] or "unknown"
    if not title:
        title = detector_id
    locations = _aderyn_instances(issue.get("instances"))
    return Finding(
        tool="aderyn",
        detector_id=detector_id,
        severity=severity,
        confidence=FindingConfidence.UNKNOWN,
        title=title,
        description=description,
        locations=locations,
        raw_ref=raw_ref,
        extra={},
    )


def parse_aderyn_json(
    data: Any,
    *,
    raw_ref: str | None = "artifacts/static/aderyn.json",
) -> list[Finding]:
    """Parse Aderyn JSON report (``-o report.json``) into findings.

    Expected shape (Cyfrin Aderyn)::

        {
          "high_issues": {"issues": [...]},
          "low_issues": {"issues": [...]},
          ...
        }

    Each issue has ``title``, ``description``, ``detector_name``, ``instances``.
    """
    root = _as_dict(data)
    if root is None:
        return []

    findings: list[Finding] = []

    # Primary: high_issues / low_issues / medium_issues buckets.
    for key, value in root.items():
        sev = _ADERYN_BUCKET_SEVERITY.get(str(key).lower())
        if sev is None:
            continue
        issues: list[Any] = []
        if isinstance(value, dict) and isinstance(value.get("issues"), list):
            issues = value["issues"]
        elif isinstance(value, list):
            issues = value
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            finding = _parse_aderyn_issue(issue, severity=sev, raw_ref=raw_ref)
            if finding is not None:
                findings.append(finding)

    # Fallback: top-level "issues" array with optional severity field.
    if not findings and isinstance(root.get("issues"), list):
        for issue in root["issues"]:
            if not isinstance(issue, dict):
                continue
            raw_sev = str(issue.get("severity") or issue.get("impact") or "unknown")
            sev = _ADERYN_BUCKET_SEVERITY.get(raw_sev.lower(), FindingSeverity.UNKNOWN)
            # Also map plain "high"/"low" etc.
            if sev is FindingSeverity.UNKNOWN:
                sev = map_slither_impact(raw_sev)
            finding = _parse_aderyn_issue(issue, severity=sev, raw_ref=raw_ref)
            if finding is not None:
                findings.append(finding)

    return findings
