"""Validate sample payloads against JSON Schema and OpenAPI parseability."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename

from auditor.contracts import (
    ArtifactManifest,
    AuditRequest,
    AuditSubmitResponse,
    JobEvent,
    JobStatusResponse,
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(schema_path: Path) -> Draft202012Validator:
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_openapi_spec_is_valid(schemas_dir: Path) -> None:
    spec_path = schemas_dir / "openapi.yaml"
    assert spec_path.is_file()
    spec_dict, _ = read_from_filename(str(spec_path))
    validate(spec_dict)
    # Sanity: required paths present
    with spec_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    paths = set(raw["paths"])
    assert "/v1/audit" in paths
    assert "/v1/jobs/{job_id}" in paths
    assert "/v1/jobs/{job_id}/artifacts" in paths
    assert "/v1/ws/jobs/{job_id}" in paths


def test_event_samples_match_schema(schemas_dir: Path, samples_dir: Path) -> None:
    validator = _validator(schemas_dir / "job-event.schema.json")
    stream = (samples_dir / "events-stream.jsonl").read_text(encoding="utf-8")
    lines = [ln for ln in stream.splitlines() if ln.strip()]
    assert len(lines) >= 2
    for line in lines:
        event = json.loads(line)
        validator.validate(event)
        # Also load via pydantic
        JobEvent.model_validate(event)
    # Last event is terminal completed
    last = json.loads(lines[-1])
    assert last["terminal"] == "completed"


def test_manifest_samples_match_schema(schemas_dir: Path, samples_dir: Path) -> None:
    validator = _validator(schemas_dir / "artifact-manifest.schema.json")
    for name in ("manifest-completed.json", "manifest-partial-failed.json"):
        data = _load_json(samples_dir / name)
        validator.validate(data)
        ArtifactManifest.model_validate(data)


def test_job_samples_via_pydantic(samples_dir: Path) -> None:
    AuditRequest.model_validate(_load_json(samples_dir / "audit-request.json"))
    AuditSubmitResponse.model_validate(_load_json(samples_dir / "audit-submit-response.json"))
    JobStatusResponse.model_validate(_load_json(samples_dir / "job-status-running.json"))
    JobStatusResponse.model_validate(_load_json(samples_dir / "job-status-failed.json"))


def test_partial_manifest_still_valid_when_stages_skipped(
    schemas_dir: Path, samples_dir: Path
) -> None:
    """Acceptance: terminal job always has a valid manifest even if stages skip."""
    data = _load_json(samples_dir / "manifest-partial-failed.json")
    assert data["status"] == "failed"
    skipped = [s for s in data["stages"] if s["status"] == "skipped"]
    assert len(skipped) >= 1
    _validator(schemas_dir / "artifact-manifest.schema.json").validate(data)
