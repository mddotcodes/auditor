# ADR 0001: Orchestration language and toolchain

| Field | Value |
|-------|--------|
| Status | Accepted |
| Date | 2026-07-29 |
| Milestone | M0.2 |

## Context

The open-source Auditor engine must orchestrate Foundry (`forge`), Slither, optional LLM API calls, a REST/WebSocket control plane, and job lifecycle (timeouts, artifacts, events).

We need a single primary language so contributors do not maintain a dual runtime, and so Slither integration stays natural (Slither is a Python project).

## Decision

**Primary language: Python 3.12+**

| Concern | Choice |
|---------|--------|
| Orchestration service | Python |
| Package layout | `src/auditor/` (src-layout) |
| Static analysis tool | Slither (invoked as library and/or CLI) |
| Compile / test / fuzz | Foundry CLI (`forge`, `cast` as needed) — external process |
| Node.js | **Not** a runtime dependency; avoid unless a future optional tool forces it |
| Lint / format | [Ruff](https://docs.astral.sh/ruff/) (format + lint) |
| Type check | [mypy](https://mypy.readthedocs.io/) (strict over package code) |
| Tests | [pytest](https://pytest.org/) |
| Task runner | GNU `make` (`fmt`, `lint`, `test`, `build-image`) |
| Dependency locking | `pyproject.toml` for project metadata; optional lockfile later (`uv.lock` or `requirements.txt` export) |

## Consequences

### Positive

- First-class Slither interop without a subprocess-only mindset
- One contributor onboarding path (Python + Docker + Foundry in the image)
- Ruff keeps CI fast and config small (no separate Black/isort stack)

### Negative / tradeoffs

- Foundry remains a binary dependency (image/local install), not a Python package
- Contributors still need Docker for full fidelity runs
- Typing around third-party crypto/tooling CLIs will rely on wrappers and careful subprocess contracts

### Explicit non-choices

- **TypeScript/Node as primary** — weaker Slither fit; dual ecosystem for little gain
- **Go/Rust as primary** — excellent for isolation daemons later, but slower to integrate Slither and ship the MVP pipeline
- **Poetry-only or Pipenv-only workflows** — stick to standards (`pyproject.toml` + pip/uv); do not require Poetry

## Follow-ups

- M1.x: Docker image pins Python, Foundry, and Slither versions together
- M2.x: OpenAPI schemas become the cross-language contract for any future non-Python clients
- Revisit only if a hard requirement appears that Python cannot meet (e.g. mandatory sandbox supervisor in another language as a sidecar)
