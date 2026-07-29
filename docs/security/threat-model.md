# Threat model: execution engine

**Scope:** the open-source Auditor container and in-process job supervisor.  
**Not in scope:** SaaS multi-tenancy control planes, payment abuse, or browser XSS.

## Assets

| Asset | Why it matters |
|-------|----------------|
| Host / sibling containers | Untrusted Solidity and LLM-generated tests must not escape |
| API keys (LLM) | Present only when the operator injects them |
| Job inputs & outputs | Integrity of findings and fingerprints |
| Engine availability | Hang/fork bombs must not wedge the node |

## Actors

1. **Contract author (local trust):** runs their own code; still treat codegen as hostile.
2. **Remote submitter (cloud):** fully untrusted source, deps, and prompts.
3. **LLM provider:** returns untrusted test code and patches.
4. **Operator / orchestrator:** configures limits, network, keys.

## Trust boundaries

```text
                    ┌─────────────────────────────┐
  untrusted input → │  Auditor container (engine)  │ → artifacts
                    │  forge / slither / pytest   │
                    └─────────────┬───────────────┘
                                  │ cgroup, caps, network, RO root
                    ┌─────────────▼───────────────┐
                    │  Host runtime (Docker/K8s)  │
                    └─────────────────────────────┘
```

- **Inside the job workdir:** untrusted.
- **Image root filesystem:** trusted build artifact; should be **read-only** at runtime.
- **Orchestrator network / Docker socket:** never mounted into the engine.

## Abuse cases

| ID | Scenario | Impact | Mitigations |
|----|----------|--------|-------------|
| A1 | Infinite loop in fuzz test / malicious contract | CPU hang | Wall-clock job timeout; cgroup CPU; kill process **group** |
| A2 | Fork bomb in generated test setup | PID / memory exhaustion | `--pids-limit`; memory cgroup; timeout |
| A3 | Write to host paths via path traversal | Host integrity | Path sandbox in `auditor.ingest`; RO root; job-only mounts |
| A4 | SSRF / data exfil via `forge` hooks or tests | Secret/network abuse | Default `--network=none`; LLM egress only on demand |
| A5 | Container breakout via kernel bug + caps | Host compromise | `--cap-drop=ALL`; `no-new-privileges`; no `--privileged`; no Docker socket |
| A6 | LLM auto-fix rewrites outside workdir | Supply-chain / host files | Apply patches only under job root (pipeline invariant) |
| A7 | Resource starvation of co-located jobs | DoS | Per-container memory/CPU; one heavy job per container in cloud |

## Security controls (mapped)

| Control | Enforced by | Code / docs |
|---------|-------------|-------------|
| Wall-clock timeout + group kill | Engine | `auditor.security.process` |
| Optional RLIMIT_AS / RLIMIT_CPU | Engine (best-effort) | `SecurityConfig` + `preexec_fn` |
| Memory / CPU / pids cgroup | Runtime | `docs/security/runtime-defaults.md` |
| Read-only root + tmpfs workdirs | Runtime | same |
| Capability drop | Runtime | same |
| Network deny by default | Runtime + phase policy | `NetworkPolicy` |
| Non-root UID | Image + runtime | Docker image `USER 10001` |

## Residual risk

- Kernel exploits may still break containment; keep the host patched.
- `RLIMIT_AS` behavior differs across OS/libc; **cgroup memory is authoritative**.
- LLM HTTPS allowlisting is not a substitute for treating model output as code execution.
- Exact bytecode fingerprinting does not prove the *auditor* was honest—only that artifacts match a build.

## Reporting

See [SECURITY.md](../../SECURITY.md). Sandbox bypasses and timeout failures are security-relevant.
