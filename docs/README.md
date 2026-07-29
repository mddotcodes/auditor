# Documentation

Public project documentation lives in this directory.

| Path | Contents |
|------|----------|
| [decisions/](decisions/) | Architectural decision records (ADRs) |
| [security/](security/) | Threat model and runtime isolation defaults |
| [ingest.md](ingest.md) | Source ingestion, gist fetch opt-in, limits, Foundry materialization |
| [../schemas/](../schemas/) | Public OpenAPI + JSON Schema contracts (v1) |
| Pipeline code | `src/auditor/pipeline/` — profiles, stages, runner, metrics |

Docker image notes: [../docker/README.md](../docker/README.md).
