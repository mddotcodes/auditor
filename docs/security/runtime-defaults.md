# Runtime security defaults

How to run the Auditor engine so untrusted compile/test workloads stay contained.

The Python package enforces **in-process** controls (timeouts, process-group kill, optional rlimits). The **container runtime** must enforce cgroups, capabilities, network, and filesystem policy.

## Hard requirements

1. **Never** `--privileged`
2. **Never** mount the Docker/Podman socket
3. **Never** run as root in production (`USER` 10001 in the image)
4. **Always** set a wall-clock budget (`AUDIT_TIMEOUT_SECONDS`, default `300`)
5. **Default network deny** for compile / Slither / `forge test`

## Recommended `docker run`

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --tmpfs /work:rw,exec,nosuid,size=2g \
  --tmpfs /artifacts:rw,noexec,nosuid,size=512m \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --network none \
  --memory 2g \
  --cpus 2 \
  --pids-limit 256 \
  --user 10001:10001 \
  -e AUDIT_TIMEOUT_SECONDS=300 \
  -e AUDIT_JOB_ROOT=/work/jobs \
  -e AUDIT_NETWORK_POLICY=deny \
  auditor:local \
  versions
```

Generate the flag list from code (same defaults):

```python
from auditor.security import SecurityConfig
print(" ".join(SecurityConfig.from_env().docker_run_flags()))
```

## Compose

Local compose files should mirror these constraints (see `docker-compose.yml` when present). A minimal security snippet:

```yaml
security_opt:
  - no-new-privileges:true
cap_drop:
  - ALL
read_only: true
tmpfs:
  - /tmp:size=256m,noexec,nosuid
  - /work:size=2g,exec,nosuid
  - /artifacts:size=512m,noexec,nosuid
mem_limit: 2g
cpus: 2
pids_limit: 256
user: "10001:10001"
network_mode: none   # override when LLM egress is required
```

## Environment variables

| Variable | Default | Role |
|----------|---------|------|
| `AUDIT_TIMEOUT_SECONDS` | `300` | Hard wall-clock job budget; kills process **tree** |
| `AUDIT_TERM_GRACE_SECONDS` | `2` | SIGTERM → wait → SIGKILL |
| `AUDIT_JOB_ROOT` | `/work/jobs` | Per-job workspace root |
| `AUDIT_NETWORK_POLICY` | `deny` | `deny` \| `allow_llm` \| `allow_fetch` |
| `AUDIT_MEMORY_LIMIT_BYTES` | `2147483648` | Hint + optional `RLIMIT_AS` for children |
| `AUDIT_CPU_LIMIT` | `2` | Documented `--cpus` value |
| `AUDIT_RLIMIT_CPU_SECONDS` | unset | Optional `RLIMIT_CPU` for children |
| `AUDIT_MAX_PIDS` | `256` | Documented `--pids-limit` |
| `AUDIT_READ_ONLY_ROOT` | `true` | Expect read-only root |
| `AUDIT_DROP_CAPABILITIES` | `true` | Expect `--cap-drop=ALL` |
| `AUDIT_NO_NEW_PRIVILEGES` | `true` | Expect `no-new-privileges` |
| `AUDIT_MAX_SOURCE_BYTES` | `2000000` | Max total UTF-8 bytes for inline sources (ingest) |
| `AUDIT_MAX_SOURCE_FILES` | `200` | Max files in an inline sources map (ingest) |
| `AUDIT_MAX_FILE_BYTES` | `512000` | Max UTF-8 bytes per source file (ingest) |
| `AUDIT_MAX_SOURCE_LOC` | `50000` | Max estimated lines across sources (ingest) |

## Network policy by phase

| Phase | Policy | Runtime expectation |
|-------|--------|---------------------|
| Materialize local sources | `deny` | offline |
| `forge build` | `deny` | offline (vendored libs) |
| Slither | `deny` | offline |
| LLM test generation / auto-fix | `allow_llm` | HTTPS to provider only |
| `forge test` / fuzz | `deny` | offline |
| Optional gist fetch | `allow_fetch` | short window, size-capped |

### Patterns for LLM egress

**A. Split containers (preferred in cloud)**  
Static/compile/test container: `--network=none`.  
LLM helper (or orchestrator): has keys + network; writes `.t.sol` into the job volume; test container stays offline.

**B. Single container, temporary network**  
Start with network for LLM, then re-exec tests under `network none` (two `docker run`s sharing a volume). Avoid leaving LLM keys in an online sandbox that also executes fuzz tests if you can split them.

**C. Operator-managed allowlist network**  
Attach a custom bridge/CNI that only routes to the LLM provider. Still treat model output as untrusted code.

## Process-tree timeout (engine)

All tool invocations should use:

```python
from auditor.security import SecurityConfig, run_command

result = run_command(
    ["forge", "build"],
    cwd=job_dir,
    config=SecurityConfig.from_env(),
    timeout_seconds=120,  # optional per-stage budget ≤ job budget
)
```

On expiry, the supervisor:

1. `SIGTERM` to the process group  
2. waits `AUDIT_TERM_GRACE_SECONDS`  
3. `SIGKILL` to the process group  
4. raises `CommandTimeoutError`

This covers hung `forge` children and accidental process leaks better than killing only the parent PID.

## Filesystem

| Path | Mode | Notes |
|------|------|-------|
| `/` (image) | read-only | `--read-only` |
| `/tmp` | tmpfs, `noexec` | temp only |
| `/work` (or job root) | tmpfs/volume, `exec` | compile needs exec for some toolchains |
| `/artifacts` | tmpfs/volume, prefer `noexec` | outputs |

Do not bind-mount host home directories. Do not mount Docker credentials.

## Resource exhaustion notes

- **Fuzz loops:** bound `forge` fuzz runs in pipeline config; rely on wall-clock timeout as backstop.
- **Pathological contracts:** compiler or analyzer may allocate heavily — cgroup `--memory` is the hard stop.
- **Zombies / orphans:** process-group kill + reaping in `run_command` reduces leftovers; container exit is the final cleanup.
- **Shared hosts:** prefer **one job per container** so cgroup limits equal job limits.

## Kubernetes sketch

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
resources:
  limits:
    memory: "2Gi"
    cpu: "2"
    ephemeral-storage: "2Gi"
# network: deny via NetworkPolicy default-deny + optional LLM egress policy
```

## AWS Fargate (recommended task settings)

Fargate does not support every Docker flag 1:1; map the intent:

| Docker intent | Fargate / ECS |
|---------------|---------------|
| Non-root `10001` | Task definition `user: "10001:10001"` |
| Memory / CPU | Task size (e.g. `2048` CPU units, `4096` MiB) |
| Read-only root | `readonlyRootFilesystem: true` |
| Drop capabilities | Linux parameters: drop `ALL` (platform support varies) |
| No new privileges | Prefer without privileged; do not request elevated |
| Network deny | Private subnet + security group deny-all egress; allow only LLM/HTTPS when needed |
| Job isolation | **One audit job per task** (do not multiplex tenants) |
| Wall-clock budget | `AUDIT_TIMEOUT_SECONDS` + ECS `stopTimeout` / task timeout |
| Logs | `AUDIT_LOG_FORMAT=json` → awslogs / FireLens |
| Metrics | Sidecar or AMP scrapes task IP `:8080/metrics` when using `serve` |

Example task environment (one-shot CLI job):

```json
{
  "environment": [
    { "name": "AUDIT_LOG_FORMAT", "value": "json" },
    { "name": "AUDIT_TIMEOUT_SECONDS", "value": "300" },
    { "name": "AUDIT_JOB_ROOT", "value": "/work/jobs" },
    { "name": "AUDIT_NETWORK_POLICY", "value": "deny" },
    { "name": "AUDIT_PROFILE", "value": "static" }
  ],
  "user": "10001:10001",
  "readonlyRootFilesystem": true
}
```

Mount a writable volume (EFS or ephemeral) at `/work` for job artifacts. Prefer
**no public IP** for static audits; add egress only for LLM profiles.

Command override for batch:

```text
auditor-cli run /corpus/Contract.sol --profile static --no-llm --json --job-root /work/jobs
```

Exit codes: `0` completed, `1` failed, `2` usage, `3` timed out, `4` cancelled
(see [`../observability.md`](../observability.md)).

## Google Cloud Run (Service vs Job)

### Cloud Run **Job** (one-shot audit — preferred for batch)

- Set container command to `auditor-cli run … --json` (or entrypoint + args).
- CPU / memory: start at **2 vCPU / 2 GiB**; raise for large corpora.
- Timeout: match or exceed `AUDIT_TIMEOUT_SECONDS` (Cloud Run job task timeout).
- Secrets: mount LLM keys as Secret Manager env vars (never bake into image).
- Volumes: GCS fuse or mounted bucket for sources + job output if needed.
- Network: Serverless VPC connector + firewall deny egress for static; allow
  HTTPS to LLM providers only when `AUDIT_PROFILE=default` and keys present.
- Logs: `AUDIT_LOG_FORMAT=json` → Cloud Logging (JSON payload parseable).
- Success criteria: **task exit code `0`**.

### Cloud Run **Service** (long-lived `serve` API)

```bash
# Conceptual — adjust project/region/image
gcloud run deploy auditor-engine \
  --image REGION-docker.pkg.dev/PROJECT/auditor/auditor:TAG \
  --port 8080 \
  --cpu 2 --memory 2Gi \
  --no-allow-unauthenticated \
  --set-env-vars "AUDIT_LOG_FORMAT=json,AUDIT_METRICS_ENABLED=true,AUDIT_TIMEOUT_SECONDS=300,AUDIT_MAX_INFLIGHT_JOBS=1" \
  --max-instances 10 \
  --concurrency 1
```

| Setting | Recommendation |
|---------|----------------|
| `--concurrency` | **1** (one heavy job per instance; pair with `AUDIT_MAX_INFLIGHT_JOBS=1`) |
| CPU always allocated | Prefer **yes** for forge/slither CPU spikes |
| Auth | Cloud IAM / internal mesh; optional `AUDIT_API_TOKEN` inside app |
| `/metrics` | Scrape via private networking; do not put metrics on a public URL |
| `/healthz` | Use as startup/liveness probe path |
| Execution env | 2nd gen; non-root image user already `10001` |

Do **not** use `--privileged` or host Docker socket on either platform.

## Verification checklist

- [ ] `docker run` without network can still `forge --version` / `slither --version`
- [ ] A deliberate hang (`sleep 600`) under `run_command(..., timeout_seconds=1)` is killed and raises `CommandTimeoutError`
- [ ] Container is not privileged and has no Docker socket
- [ ] Root filesystem is read-only; jobs only write under `/work` / `/artifacts`
