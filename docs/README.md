# Documentation

Public project documentation lives in this directory.

| Path | Contents |
|------|----------|
| [decisions/](decisions/) | Architectural decision records (ADRs) |
| [security/](security/) | Threat model and runtime isolation defaults (Docker, Fargate, Cloud Run) |
| [observability.md](observability.md) | JSON logs, Prometheus `/metrics`, CLI exit codes for orchestrators |
| [ingest.md](ingest.md) | Source ingestion, gist fetch opt-in, limits, Foundry materialization |
| [testing.md](testing.md) | Unit vs integration vs corpus vs container; how to run tests |
| [test-reports/](test-reports/) | Test report format, sample corpus summary, regenerate via `make test-report` |
| [budgets.md](budgets.md) | Default CPU/RAM/timeout/fuzz budgets and CI policy |
| [release.md](release.md) | How to tag releases and publish the GHCR image |
| [../schemas/](../schemas/) | Public OpenAPI + JSON Schema contracts (v1) |
| [../CHANGELOG.md](../CHANGELOG.md) | Keep a Changelog release notes |
| Pipeline code | `src/auditor/pipeline/` — profiles, stages, runner, metrics |

Docker image notes: [../docker/README.md](../docker/README.md).
