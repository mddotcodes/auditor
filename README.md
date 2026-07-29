# Auditor

**Auditor** is a self-contained engine that audits Solidity smart contracts and streams the results as structured technical output.

Point it at your contract (or a small Foundry project), and it will:

1. **Compile** with Foundry  
2. **Scan** with [Slither](https://github.com/crytic/slither) for known vulnerability classes  
3. **Generate** fuzz and invariant tests with an LLM (optional — your API key)  
4. **Run** those tests under Foundry  
5. **Emit** machine-readable artifacts: findings JSON, generated `.t.sol` files, coverage, and a bytecode fingerprint for later on-chain matching  

Everything runs inside one Docker image so the toolchain is pinned and your host stays clean.

## What you get

| Output | Description |
|--------|-------------|
| Static findings | Normalized Slither results (reentrancy, access control, etc.) plus raw tool logs |
| Generated tests | Foundry fuzz / invariant tests targeting state-changing functions |
| Test report | Pass/fail summary, failing inputs when available, coverage when enabled |
| Bytecode fingerprint | Compiler settings + bytecode / metadata hashes for exact-match verification after deploy |
| Live progress | Stage updates over HTTP + WebSocket while a job runs |

## Who it’s for

- Developers who want a **local, repeatable** audit pipeline without assembling Foundry + Slither + scripts by hand  
- Teams that want **raw technical artifacts** they can pipe into CI, custom UIs, or their own review process  
- Anyone who wants **optional LLM-assisted test generation** without baking a vendor into the core scanner  

## Quickstart

> Status: early development — the image and API are not published yet. The flow below is the intended UX.

```bash
docker run --rm -p 8080:8080 \
  -e OPENROUTER_API_KEY=sk-… \   # optional; omit for static-only
  ghcr.io/<org>/auditor:latest
```

Submit a contract:

```bash
curl -s -X POST http://localhost:8080/v1/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "sources": {
      "src/Token.sol": "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\n..."
    }
  }'
# → { "job_id": "…" }
```

Stream progress:

```text
WS  /v1/ws/jobs/{job_id}
GET /v1/jobs/{job_id}
```

When the job finishes, fetch artifacts (findings, tests, fingerprint) from the job result.

### CLI (planned)

```bash
auditor-cli run ./src/Token.sol
auditor-cli run ./my-foundry-project
auditor-cli metrics ./src/Token.sol   # quick size / complexity estimate
```

## Modes

| Mode | Needs | Behavior |
|------|--------|----------|
| **Static** | Docker only | Compile + Slither + fingerprint |
| **Full** | Docker + LLM API key | Static path, then generate and run fuzz/invariant tests |
| **Metrics** | Docker only | Fast pre-check: LOC, complexity estimate, rough token estimate — no full audit |

Supported LLM providers (via env): OpenAI, Anthropic, OpenRouter.

## Pipeline

```text
input (sources | project | gist)
        │
        ▼
   materialize Foundry project
        │
        ▼
   forge build  ──fail──►  optional LLM auto-fix (≤3)  ──or──► fail job
        │
        ▼
   Slither (JSON findings)
        │
        ▼
   [if API key] LLM writes .t.sol  ──compile feedback loop ≤3──► forge test / fuzz
        │
        ▼
   artifacts + bytecode fingerprint
```

Hard wall-clock timeout on every job so runaway fuzz or bad tests cannot hang forever.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | — | OpenAI for test generation / auto-fix |
| `ANTHROPIC_API_KEY` | — | Anthropic |
| `OPENROUTER_API_KEY` | — | OpenRouter |
| `AUDIT_TIMEOUT_SECONDS` | `300` | Kill the job after this many seconds |
| `AUTO_FIX_COMPILE` | `false` | Let the LLM attempt compile fixes (max 3) |

Use a local `.env` for keys; never commit them. A `.env.example` will ship with the first runnable release.

## Security defaults

Contracts and generated tests are untrusted code. The container is built to run them under constraints:

- Non-root user  
- Per-job work directories (no sharing between concurrent jobs)  
- Configurable CPU / memory limits via normal Docker flags  
- Network off for compile/test by default; LLM calls only when you supply a key  

Recommended local run:

```bash
docker run --rm -p 8080:8080 \
  --memory=2g --cpus=2 \
  --read-only --tmpfs /tmp --tmpfs /work \
  -e OPENROUTER_API_KEY=… \
  ghcr.io/<org>/auditor:latest
```

## Development

```bash
git clone https://github.com/<org>/auditor.git
cd auditor

make lint          # format + static checks
make test          # unit / integration
make build-image   # local Docker image
```

Toolchain and layout land with the first implementation PRs.

## Roadmap (high level)

- [ ] Runnable Docker image + `POST /v1/audit`  
- [ ] Slither findings + artifact manifest  
- [ ] WebSocket job stream  
- [ ] LLM test generation + fuzz execution  
- [ ] CLI  
- [ ] Published GHCR image and semver tags  

## Contributing

Bug reports and PRs are welcome. For larger design changes, open an issue first so we can align on the public API and artifact schemas.

## License

[MIT](LICENSE)
