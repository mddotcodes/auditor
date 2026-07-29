# Testing methodology

How Auditor is tested, what each layer covers, and which commands to run locally or in CI.

## Layers

| Layer | What it proves | Typical location | Default in CI? |
|-------|----------------|------------------|----------------|
| **Unit** | Pure Python logic, parsers, models, path/limit guards, stage orchestration with mocks | `tests/test_*.py` | Yes |
| **Integration** | Multi-component flows that need real tools on `PATH` (e.g. `forge`, Slither) or a local HTTP app | same tree; `@pytest.mark.integration` | Collected; often skip-if-missing |
| **Corpus** | Known-vulnerable / edge-case Solidity fixtures produce expected finding families | `examples/corpus/`, `tests/fixtures/vuln/` (when present) | Via integration tests when tools exist |
| **Container** | Image build + hardened run against samples / e2e smoke | `docker/`, `scripts/run-local.sh`, later e2e jobs | Image build in CI |

Default contributor loop:

```bash
make lint && make test
```

`make test` runs the default suite via `python -m pytest` (see `[tool.pytest.ini_options]` in `pyproject.toml`).

## Unit tests

Fast, offline, no network. Prefer pure functions and in-process fakes over external processes.

```bash
make test
# equivalent:
python -m pytest
```

Generate a markdown summary under `docs/test-reports/`:

```bash
make test-report
# or:
./scripts/gen-test-report.sh
./scripts/gen-test-report.sh -k static   # pass extra pytest args
```

## Integration tests

Markers are registered in `pyproject.toml` (`--strict-markers`):

| Marker | Meaning |
|--------|---------|
| `integration` | Needs forge/Slither or full pipeline (skipped if tools missing) |
| `slow` | Longer-running tests |

Select or exclude explicitly:

```bash
pytest -m integration
pytest -m slow
pytest -m "not slow"           # keep local loops fast
pytest -m "not integration"    # pure unit-ish subset
```

Some tests also use `@pytest.mark.skipif(shutil.which("forge") is None, ...)` so the default suite stays green without optional tools.

## Corpus / fixture tests

Layout:

```text
examples/corpus/              # human-browsable demos + expected.json
tests/fixtures/vuln/          # CI-oriented copies (when present)
examples/contracts/           # small demos (e.g. VulnerableBank.sol)
```

When corpus cases exist under `examples/corpus/` (or `tests/fixtures/vuln/`):

- Run the static / offline profile against each fixture.
- Assert `expected.json` fields (e.g. `min_findings`, `detector_families`) when Slither is available.
- Prefer offline vendor remappings; do not require mainnet RPC.

Corpus pytest modules typically carry `@pytest.mark.integration` and skip when forge/Slither or cases are missing.

### Static corpus batch (free calibration)

Multi-contract **static-only** runs over `examples/corpus/*` with **no LLM / OpenRouter**. Prefer Docker `auditor:local`.

```bash
make static-corpus
# or:
./scripts/batch-static-corpus.sh
./scripts/batch-static-corpus.sh --include-batch   # also examples/batch/*.sol
```

Writes:

- `work/static-corpus/summary.jsonl` — per-case machine summary
- `docs/test-reports/static-corpus-latest.md` — table (label, compile ok, findings, tools_run, detectors)

No API keys required. Image is built if missing; if Docker is unavailable, the script falls back to host `auditor-cli` with a warning.

## Container tests

Image-level checks (build, hardened run, later e2e against examples/corpus) are separate from the default unit suite.

```bash
make build-image
./scripts/run-local.sh versions
```

See [docker/README.md](../docker/README.md) and [security/runtime-defaults.md](security/runtime-defaults.md).

## CI policy: no LLM, no RPC

GitHub Actions (`.github/workflows/ci.yml`) must stay **offline** with respect to external model and chain services:

| Concern | CI rule |
|---------|---------|
| **LLM** | Do not call provider APIs. No secrets for model keys in the default job. Stages that need an LLM are unit-tested with mocks or skipped. |
| **RPC / mainnet** | Do not hit public or private Ethereum RPC endpoints. No live chain forks in PR CI. |
| **Network fetch** | Gist/source fetch is opt-in in product code; tests must not require network. |

PR CI runs `make lint` and `make test` (plus Docker image build when a Dockerfile is present), then optionally regenerates and **uploads** `docs/test-reports/latest.md` as a workflow artifact. Optional slower jobs (full corpus with Slither, container e2e) may be added later but must not require LLM or RPC.

## Regenerating reports

See [test-reports/README.md](test-reports/README.md). Prefer CI workflow artifacts over committing every `latest.md` run into git.

## Related

- [examples/README.md](../examples/README.md) — sample contracts
- [dependencies.md](dependencies.md) — offline remappings / vendor policy
- [schemas/README.md](../schemas/README.md) — contract schema tests
