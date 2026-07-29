# Auditor

**Auditor** is a local-first engine that audits Solidity contracts and writes machine-readable artifacts.

Point it at a `.sol` file or small Foundry tree and it will materialize a project, compile with Foundry, run static analysis and metamorphic heuristics, optionally generate LLM-assisted tests, fuzz with Forge, and finalize with a bytecode fingerprint.

## Quickstart (< 5 minutes)

### 1. Docker audit (recommended — no host forge/slither)

Requires **Docker** only. Builds `auditor:local` on first use if missing.

```bash
# Static-only (no API key needed)
./scripts/user-audit.sh examples/contracts/SafeCounter.sol

# Explicit profile / timeout / host job directory
./scripts/user-audit.sh --profile static --timeout 300 \
  --job-root ./work/user-jobs \
  examples/contracts/SafeCounter.sol

# LLM-enabled (default profile when a key is set; loads .env if present)
cp .env.example .env   # set OPENROUTER_API_KEY=… and optional AUDIT_LLM_* dual settings
./scripts/user-audit.sh examples/corpus/reentrancy/src/ReentrancyBank.sol
# or force static even with keys:
./scripts/user-audit.sh --no-llm examples/contracts/SafeCounter.sol
```

On success the script prints the **host** path to job artifacts under `--job-root` (default `./work/user-jobs/<job_id>/`):

| Path | What it is |
|------|------------|
| `artifacts/manifest.json` | Job artifact index |
| `artifacts/static/findings.json` | Normalized findings (Slither, Aderyn, metamorphic, …) |
| `artifacts/fingerprint.json` | Bytecode / compiler fingerprint for later matching |

Exit codes: **`0`** completed · **`1`** job failed / timed out / cancelled · **`2`** usage error (bad path, invalid profile, …).

Image override: `AUDITOR_IMAGE` (default `auditor:local`). Build manually: `make build-image`.

### 2. Docker serve (HTTP API)

```bash
make build-image
docker run --rm -p 8080:8080 \
  --memory=2g --cpus=2 \
  -e AUDIT_PROFILE=static \
  -e AUDIT_JOB_ROOT=/work/jobs \
  auditor:local serve
```

Hardened run flags: [`docs/security/runtime-defaults.md`](docs/security/runtime-defaults.md). Low-level image checks: [`./scripts/run-local.sh`](scripts/run-local.sh).

### 3. HTTP API (curl)

With the server up:

```bash
curl -s -X POST http://localhost:8080/v1/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "sources": {
      "src/Token.sol": "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\ncontract Token {}"
    },
    "options": { "enable_llm_tests": false }
  }'
# → { "job_id": "…", "status": "queued" }

curl -s http://localhost:8080/v1/jobs/<job_id>
# Live events: WebSocket /v1/ws/jobs/<job_id>
curl -s http://localhost:8080/healthz
```

OpenAPI contract: [`schemas/openapi.yaml`](schemas/openapi.yaml).

---

## What you get

| Output | Description |
|--------|-------------|
| Static findings | Slither + Aderyn (when installed), normalized into `findings.json` |
| Metamorphic signals | Offline heuristics (SELFDESTRUCT / DELEGATECALL / CREATE2-style patterns) |
| Generated tests | Optional LLM-written Foundry fuzz / invariant tests (`default` / `deep`) |
| Fuzz report | Forge test results under `artifacts/fuzz/` when fuzz runs |
| Bytecode fingerprint | Compiler settings + hashes for post-deploy matching |
| Live progress | Stage events on CLI stdout (or JSON lines); HTTP + WebSocket on the server |

Sample contracts: [`examples/contracts/`](examples/contracts/). Sample artifact shapes: [`examples/artifacts/`](examples/artifacts/), schemas under [`schemas/`](schemas/).

## Pipeline

```text
input (.sol | project dir | API sources / gist)
        │
        ▼
   materialize  →  Foundry project on disk
        │
        ▼
   compile      →  forge build
        │
        ▼
   static       →  Slither ∥ Aderyn → findings.json
        │
        ▼
   metamorphic  →  mutability / morph heuristics
        │
        ▼
   [default+] llm_tests  →  optional LLM .t.sol generation
        │
        ▼
   [default+] fuzz       →  forge test / fuzz
        │
        ▼
   [deep]     echidna    →  when available / properties exist
        │
        ▼
   finalize     →  manifest + fingerprint.json
```

Jobs have a wall-clock timeout (`AUDIT_TIMEOUT_SECONDS`, default `300`) so runaway fuzz cannot hang forever.

### Profiles

| Profile | Stages (beyond materialize → compile) | Typical use |
|---------|----------------------------------------|-------------|
| **`static`** | static → metamorphic → finalize | Offline / CI / no API keys |
| **`default`** | + llm_tests (if key) + forge fuzz | Local deeper run |
| **`deep`** | + echidna when available | Heavier analysis |

- **`user-audit.sh`**: defaults to **`static`** with no LLM key; **`default`** when a key is present. Override with `--profile` or force offline with `--no-llm`.
- **Host CLI** defaults to **`static`** unless you pass `--profile` or set `AUDIT_PROFILE`.
- **Server / env** `AUDIT_PROFILE` defaults to **`default`** when set via the env helper; pin `AUDIT_PROFILE=static` for deterministic no-LLM deploys.

## First LLM run (optional)

Costs real API spend. Start with **one** contract and inspect artifacts before more runs.

**Plan → code (optional dual):** not consensus. A stronger model writes a **test plan**; a cheaper model **implements** Foundry tests and compile repairs. See [`.env.example`](.env.example).

```bash
cp .env.example .env
# OPENROUTER_API_KEY=…
# AUDIT_LLM_DUAL=true
# AUDIT_LLM_PLAN_MODEL=deepseek/deepseek-v4-pro
# AUDIT_LLM_CODE_MODEL=deepseek/deepseek-v4-flash
set -a && source .env && set +a

# Preferred: Docker dual-profile helper (calls user-audit.sh)
./scripts/first-llm-run.sh examples/corpus/reentrancy/src/ReentrancyBank.sol
# equivalent:
./scripts/user-audit.sh --profile default examples/corpus/reentrancy/src/ReentrancyBank.sol
```

Provider selection (first key wins): `OPENROUTER_API_KEY` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY`.

## Configuration (common)

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | LLM test generation |
| `AUDIT_LLM_MODEL` | provider default | Model id override |
| `AUDIT_LLM_DUAL` / `AUDIT_LLM_PLAN_MODEL` / `AUDIT_LLM_CODE_MODEL` | see `.env.example` | Plan→code dual models |
| `AUDIT_PROFILE` | see Profiles | Pipeline depth |
| `AUDIT_TIMEOUT_SECONDS` | `300` | Job wall-clock limit |
| `AUDIT_JOB_ROOT` | `./work/jobs` (host CLI) / `/work/jobs` (image) | Job workspaces inside the engine |
| `--job-root` (`user-audit.sh`) | `./work/user-jobs` | Host directory for Docker-mounted jobs |
| `AUDITOR_IMAGE` | `auditor:local` | Docker image for user-audit / run-local |
| `AUDIT_HOST` / `AUDIT_PORT` | `0.0.0.0` / `8080` | HTTP bind for `auditor-serve` |
| `AUDIT_API_TOKEN` | — | Optional bearer token for the API |

Never commit secrets. Copy [`.env.example`](.env.example) to `.env`.

## Security

Contracts and generated tests are untrusted code. Prefer the container with dropped caps, memory/CPU limits, and network deny for compile/test. `user-audit.sh` uses network none for static runs and only enables bridge when an LLM profile needs provider egress. Details: [`docs/security/runtime-defaults.md`](docs/security/runtime-defaults.md) and [`SECURITY.md`](SECURITY.md).

## Status

Runnable today: Docker image `auditor:local` via [`scripts/user-audit.sh`](scripts/user-audit.sh), HTTP API (`serve`), OpenAPI at [`schemas/openapi.yaml`](schemas/openapi.yaml), pipeline profiles above, and example contracts under [`examples/`](examples/). Host Python package (`auditor-cli`, `auditor-serve`) is available for development.

Published registry images (e.g. `ghcr.io/OWNER/auditor`) may appear later — until then build with `make build-image`.

Further reading: [`docs/`](docs/) (ingest, dependencies, security, ADRs), [`schemas/`](schemas/), [`docker/README.md`](docker/README.md).

## Development

Host install is for contributors (tests, lint, local CLI without Docker). End users should prefer **Docker / `user-audit.sh`** above.

```bash
git clone https://github.com/<org>/auditor.git
cd auditor
python3 -m venv .venv && source .venv/bin/activate
make install    # pip install -e ".[dev]"  — requires Python 3.12+
make lint
make test
make build-image

# Optional: host CLI (needs forge / slither / aderyn on PATH for full stages)
auditor-cli metrics examples/contracts/SafeCounter.sol
auditor-cli run examples/contracts/SafeCounter.sol
# CLI defaults to --profile static (no LLM). Explicit and equivalent:
auditor-cli run examples/contracts/SafeCounter.sol --profile static --no-llm
# Without Docker serve:
auditor-serve   # binds AUDIT_HOST / AUDIT_PORT, default 0.0.0.0:8080
```

Useful host flags: `--profile static|default|deep`, `--job-root DIR`, `--json`, `--no-llm`, `--timeout SECONDS`. See `auditor-cli --help`.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

[MIT](LICENSE)
