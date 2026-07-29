# Resource budgets & performance defaults

Defaults are sized for a **developer laptop** and a single isolated job container. Cloud orchestrators should set cgroup limits at least as tight as these.

## Container / process defaults

| Resource | Default | Env / flag |
|----------|---------|------------|
| Job wall clock | **300s** | `AUDIT_TIMEOUT_SECONDS` |
| Term grace before SIGKILL | 2s | `AUDIT_TERM_GRACE_SECONDS` |
| Memory (Docker) | **2 GiB** | `AUDIT_MEMORY_LIMIT_BYTES` / `--memory=2g` |
| CPU (Docker) | **2** | `AUDIT_CPU_LIMIT` / `--cpus=2` |
| PIDs | **256** | `AUDIT_MAX_PIDS` / `--pids-limit=256` |
| In-flight HTTP jobs | **2** | `AUDIT_MAX_INFLIGHT_JOBS` |
| Event ring buffer / job | **2000** | `AUDIT_EVENT_BUFFER_SIZE` |

See also [security/runtime-defaults.md](./security/runtime-defaults.md).

## Pipeline stage budgets (soft)

| Stage | Typical bound | Notes |
|-------|---------------|--------|
| Materialize | seconds | Size caps in `IngestLimits` |
| Compile (`forge build`) | ≤ ~180s within job | Hard-fail job on failure |
| Static (Slither ∥ Aderyn) | split of remaining stage timeout | Soft-fail per tool |
| Metamorphic | sub-second–few s | Heuristic only |
| LLM tests | API + ≤3 compile loops | Skipped without key / static profile |
| Forge fuzz | `AUDIT_FOUNDRY_FUZZ_RUNS` default **64** | Keep low in CI |
| Echidna | only `deep` / flag | Optional binary |

## Ingest guardrails (pathological input)

| Cap | Default env |
|-----|-------------|
| Max source bytes | `AUDIT_MAX_SOURCE_BYTES` (2_000_000) |
| Max files | `AUDIT_MAX_SOURCE_FILES` (200) |
| Max per file | `AUDIT_MAX_FILE_BYTES` (512_000) |
| Max LOC | `AUDIT_MAX_SOURCE_LOC` (50_000) |

## Expected wall time (envelope, laptop)

Rough **static** profile on a small fixture (e.g. reentrancy bank), tools warm:

| Case | Order of magnitude |
|------|--------------------|
| CLI static, one file | ~10–60s (forge + slither dominate) |
| Metrics only | &lt; 1s |
| Default + LLM | highly variable (API + rebuilds); budget full 300s |

Use corpus + `examples/contracts/SafeCounter.sol` for local smoke; do **one** LLM run first before batch jobs.

## CI policy

- PR: unit tests always; integration/corpus when forge present (or skip).
- No live LLM, no mainnet RPC.
- Container e2e: build image + static run or `serve` `/healthz` within runner limits.
