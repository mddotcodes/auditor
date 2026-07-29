# Public I/O contracts (schema version **1**)

Machine contracts for the Auditor execution engine. Implementors (HTTP server, CLI, cloud orchestrator) should treat these as the source of truth.

| File | Purpose |
|------|---------|
| [`openapi.yaml`](./openapi.yaml) | REST + WebSocket surface (`/v1/...`) |
| [`job-event.schema.json`](./job-event.schema.json) | Live event envelope (WS / `events.jsonl`) |
| [`artifact-manifest.schema.json`](./artifact-manifest.schema.json) | `artifacts/manifest.json` |
| [`samples/`](./samples/) | Valid example payloads |

Python models live in `auditor.contracts` and stay aligned with these files.

## Versioning

- Payload field `schema_version` is the string `"1"`.
- HTTP paths are under `/v1/`.
- **Breaking** enum/path/field changes require `/v2` and/or `schema_version: "2"`.
- Additive optional fields are non-breaking when clients ignore unknowns (OpenAPI responses remain closed in v1 models; prefer version bump for strict clients).

## Pipeline stages (frozen)

```text
materialize → compile → static → llm_tests → fuzz → finalize
```

| Stage | Role |
|-------|------|
| `materialize` | Normalize inputs into a Foundry project tree |
| `compile` | `forge build` |
| `static` | Slither + normalized findings |
| `llm_tests` | Optional LLM-generated `.t.sol` (may skip) |
| `fuzz` | `forge test` / fuzz (may skip) |
| `finalize` | Manifest, fingerprint, package artifacts |

## Job status vs terminal events

| `JobStatus` | Meaning |
|-------------|---------|
| `queued` | Accepted, not started |
| `running` | Pipeline active |
| `completed` | Finished successfully |
| `failed` | Finished with error |
| `timed_out` | Wall-clock budget exhausted |
| `cancelled` | Explicit cancel |

Terminal event field `terminal`: `completed` | `failed` | `timed_out` | `cancelled`.

## Job directory layout

Relative to `{AUDIT_JOB_ROOT}/{job_id}/` (see `auditor.contracts.layout.JOB_LAYOUT`):

```text
project/                         # Foundry project
artifacts/
  manifest.json                  # always present after terminal
  events.jsonl                   # optional durable stream log
  meta/pipeline.json
  compile/forge-build.log
  compile/bytecode/
  compile/abi/
  static/slither.json
  static/findings.json
  llm_tests/generated/*.t.sol
  fuzz/forge-test.json
  fuzz/coverage/
  fingerprint.json
  source/                        # optional source snapshot
```

Every listed artifact in the manifest includes `sha256` and `size_bytes`.

## Samples

```bash
# Validate samples against JSON Schema (dev deps)
python -m pytest tests/test_contracts_schema.py
```
