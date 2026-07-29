# Security documentation

| Doc | Description |
|-----|-------------|
| [threat-model.md](./threat-model.md) | Assets, actors, abuse cases, residual risk |
| [runtime-defaults.md](./runtime-defaults.md) | Docker/K8s/Fargate/Cloud Run flags, env vars, network policy by phase |
| [../observability.md](../observability.md) | JSON logs, Prometheus metrics, batch exit codes |

Engine helpers live in the Python package `auditor.security` (timeouts, process-group kill, config)
and `auditor.observability` (structured logs, metrics, exit codes).
