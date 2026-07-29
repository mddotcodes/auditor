# Docker image (M1.1)

Version-pinned runtime image with **Foundry** (`forge`, `cast`, `anvil`), **Slither**, and the **auditor** Python package.

Pinned versions live in [`versions.env`](./versions.env) and as `ARG` defaults in [`Dockerfile`](./Dockerfile).

## Build

From the repository root:

```bash
make build-image
# equivalent:
docker build -f docker/Dockerfile -t auditor:local .
```

Requires a running Docker daemon.

## Verify toolchain

Default command prints versions and exits 0:

```bash
docker run --rm auditor:local
# or explicitly:
docker run --rm auditor:local versions
```

Manual checks:

```bash
docker run --rm auditor:local forge --version
docker run --rm auditor:local slither --version
docker run --rm auditor:local python -c "import auditor; print(auditor.__version__)"
docker run --rm auditor:local id   # should be uid=10001(auditor)
```

Print versions before any command:

```bash
docker run --rm -e AUDIT_PRINT_VERSIONS=1 auditor:local forge --version
```

## Layout

| Path / user | Purpose |
|-------------|---------|
| `USER auditor` (uid/gid **10001**) | Non-root default |
| `/work` | Job workspaces (`WORKDIR`) |
| `/work/jobs` | Default job root (`AUDIT_JOB_ROOT`) |
| `/artifacts` | Optional aggregate output |
| `/tmp` | Temp (world-writable sticky bit) |

## solc

- **Foundry** installs/compilers via `forge` / svm under the user home.
- **solc-select** provides a default `solc` (see `SOLC_VERSION` in `versions.env`) for standalone Slither runs.
- Prefer analyzing Foundry projects so Slither uses the project compilation framework.

## Security / runtime flags

Hardened `docker run` / Compose defaults (network, caps, read-only root, etc.) are documented in:

[`docs/security/runtime-defaults.md`](../docs/security/runtime-defaults.md)

## Local compose / run-local.sh

From the repo root, prefer one-shot runs (no long-running server in Phase 1):

```bash
./scripts/run-local.sh              # builds auditor:local if missing; default: versions
docker compose run --rm auditor versions
docker compose --profile llm run --rm auditor-llm versions   # bridge net for later LLM use
```

Compose file: [`../docker-compose.yml`](../docker-compose.yml). Script: [`../scripts/run-local.sh`](../scripts/run-local.sh).

## Entrypoint

- `ENTRYPOINT`: `/usr/local/bin/auditor-entrypoint`
- Default `CMD`: `versions`
- Future: `serve`, job runners, etc. — not in Phase 1.
