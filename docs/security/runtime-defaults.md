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

## Verification checklist

- [ ] `docker run` without network can still `forge --version` / `slither --version`
- [ ] A deliberate hang (`sleep 600`) under `run_command(..., timeout_seconds=1)` is killed and raises `CommandTimeoutError`
- [ ] Container is not privileged and has no Docker socket
- [ ] Root filesystem is read-only; jobs only write under `/work` / `/artifacts`
