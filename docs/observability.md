# Observability (M7.3)

Hooks for **cloud orchestrators** (Fargate, Cloud Run, Kubernetes Jobs) so they can
scrape logs and metrics without parsing human-readable CLI text.

## Structured JSON logs

Set:

```bash
export AUDIT_LOG_FORMAT=json
export AUDIT_LOG_LEVEL=info   # debug|info|warning|error
```

Application logs are emitted as **one JSON object per line** on stdout:

```json
{"ts":"2026-07-29T12:00:00.123456Z","level":"INFO","logger":"auditor.api.app","msg":"auditor API starting …","job_id":"…","profile":"default"}
```

| Field | Meaning |
|-------|---------|
| `ts` | ISO-8601 UTC timestamp |
| `level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `logger` | Python logger name |
| `msg` | Message text |
| `job_id`, `stage`, `profile`, … | Optional bound context |

Default is `AUDIT_LOG_FORMAT=text` for local development.

### Job event stream (CLI)

Separate from application logs, the CLI can stream **job events** as JSON lines:

```bash
auditor-cli run path/to/Contract.sol --json --profile static --no-llm
```

Each line is a `JobEvent` (see `schemas/job-event.schema.json`). The final line after the
stream is a small status object: `job_id`, `status`, `progress`, `error_code`, `manifest`, …

HTTP mode streams the same events over WebSocket (`/v1/ws/jobs/{job_id}`).

## Prometheus metrics

When the HTTP server is running (`auditor-serve` / `docker run … serve`):

```http
GET /metrics
```

Returns Prometheus text exposition (`text/plain; version=0.0.4`).  
Disable with `AUDIT_METRICS_ENABLED=false` (endpoint returns 404).

**Unauthenticated by design** so sidecar scrapers need no API token. Bind the engine to a
private network / mesh and do not expose `/metrics` on the public internet.

### Series

| Metric | Type | Labels | Notes |
|--------|------|--------|-------|
| `auditor_build_info` | gauge | `version` | Always 1 |
| `auditor_process_start_time_seconds` | gauge | — | Unix start time |
| `auditor_jobs_started_total` | counter | `profile` | Jobs accepted |
| `auditor_jobs_finished_total` | counter | `status`, `profile` | Terminal outcomes |
| `auditor_jobs_inflight` | gauge | — | Running jobs |
| `auditor_stage_duration_seconds` | summary | `stage` | `_sum` / `_count` |

Example scrape config (Prometheus):

```yaml
scrape_configs:
  - job_name: auditor-engine
    metrics_path: /metrics
    static_configs:
      - targets: ["auditor:8080"]
```

Cloud Monitoring / AMP / Grafana Alloy can scrape the same path.

## Process exit codes (non-server mode)

For `auditor-cli run` and Docker one-shot jobs (Fargate task, Cloud Run Job, K8s Job):

| Code | Constant | Meaning |
|-----:|----------|---------|
| **0** | `EXIT_OK` | Job `completed` (inspect findings/artifacts separately) |
| **1** | `EXIT_JOB_FAILED` | Job `failed` (compile/pipeline hard-fail) |
| **2** | `EXIT_USAGE` | Bad args / missing paths / config error — do not blind-retry |
| **3** | `EXIT_TIMED_OUT` | Wall-clock timeout — may retry with higher budget |
| **4** | `EXIT_CANCELLED` | Cancelled |

Python:

```python
from auditor.observability import exit_code_for_status, EXIT_OK
```

Orchestrators that only need success/failure: check `exit_code == 0`.

### Example one-shot Cloud Run Job

```bash
docker run --rm \
  -e AUDIT_LOG_FORMAT=json \
  -e AUDIT_PROFILE=static \
  -e AUDIT_TIMEOUT_SECONDS=300 \
  -v "$PWD/contracts:/corpus:ro" \
  -v "$PWD/jobs:/work/jobs" \
  auditor:local \
  auditor-cli run /corpus/Token.sol --profile static --no-llm --json --job-root /work/jobs
```

Parse stdout JSON lines for events; use the container exit code for terminal status.

## Health check

```http
GET /healthz
→ {"status":"ok","version":"…"}
```

Use for load balancer / task health (not a substitute for job success).

## Related

- Security / isolation flags: [`security/runtime-defaults.md`](./security/runtime-defaults.md) (includes Fargate & Cloud Run)
- OpenAPI: [`../schemas/openapi.yaml`](../schemas/openapi.yaml)
- Event schema: [`../schemas/job-event.schema.json`](../schemas/job-event.schema.json)
