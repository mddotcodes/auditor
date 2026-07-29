# Source ingestion (M3.1)

How the engine accepts Solidity sources and materializes a Foundry project under
the job layout (`{AUDIT_JOB_ROOT}/{job_id}/project/`).

## Input modes

| Mode | Status | Entry point |
|------|--------|-------------|
| Inline multi-file map `path → content` | **Supported** | `auditor.ingest.materialize_sources` |
| Uploaded zip/tarball | Stretch / not in v1 | — |
| GitHub Gist URL | **Supported (opt-in)** | `fetch_gist_sources` → `materialize_sources` |

`AuditRequest.sources` (see `auditor.contracts.jobs`) is the API-facing map.
Ingestion performs a second, stricter validation pass before any disk write.

## Layout mapping

1. Paths are normalized to relative POSIX form (`src/A.sol`).
2. If the payload already looks like Foundry (`src/`, `test/`, `lib/`, `script/`,
   or a `foundry.toml`), paths are preserved.
3. If every `.sol` file is a **flat basename** (no directory component), files
   are placed under `src/`.
4. Otherwise paths are kept as given (e.g. `contracts/Token.sol`).

Always created under the project root:

- `src/`, `test/`, `lib/`, `script/`
- `foundry.toml` (generated or validated user-supplied)

## Limits

| Environment variable | Default | Meaning |
|----------------------|---------|---------|
| `AUDIT_MAX_SOURCE_BYTES` | `2000000` | Max UTF-8 bytes across all sources |
| `AUDIT_MAX_SOURCE_FILES` | `200` | Max number of files in the map |
| `AUDIT_MAX_FILE_BYTES` | `512000` | Max UTF-8 bytes per file |
| `AUDIT_MAX_SOURCE_LOC` | `50000` | Max estimated lines (newline count) |

Exceeding any limit raises `PayloadTooLargeError` **before** writing sources.

## Path safety

Rejected with `PathTraversalError`:

- `..` segments, absolute paths, `~`, Windows drives, UNC paths
- NUL bytes, empty paths, reserved device names (`CON`, `NUL`, …)
- Writes through existing symlinks under the project directory

`JobPaths` / `JOB_LAYOUT` still apply: project lives at `project/` under the job
base. Ingestion never writes outside `job_paths.project`.

## Pragma policy

1. Scan each `.sol` file for `pragma solidity …;`.
2. Parse common forms: exact, `^`, `~`, `>=` / `>` / `<=` / `<`, and compounds
   (`>=0.8.0 <0.9.0`).
3. Intersect all ranges across the project.
4. **Empty intersection** → `PragmaConflictError` (e.g. `^0.7.0` with `^0.8.0`).
5. **Compatible ranges** → pin `solc_version` in generated `foundry.toml` to the
   effective lower bound when known (e.g. `^0.8.20` → `0.8.20`).
6. **No pragmas** → leave solc to forge auto-detect (`solc_version` unset).

Files without a pragma are allowed alongside files that have one; the
intersection is driven only by declared pragmas.

## `foundry.toml`

- **Generated (default):** minimal offline profile (`offline = true`, standard
  `src` / `test` / `lib` / `script` / `out`), optional `solc_version` pin.
- **User-supplied:** parsed with `tomllib` and checked for absolute paths,
  `..` traversal in path fields, and shell metacharacters. Unsafe configs raise
  `InvalidFoundryConfigError`. Remapping policy and vendored libs are **M3.2**.

## Integration with M3.2 (deps)

After writing `foundry.toml`, materialize optionally calls:

```python
from auditor.deps import apply_default_vendor_libs  # provided by M3.2
apply_default_vendor_libs(project_dir)
```

If `auditor.deps` is not installed, the hook is a no-op. Callers can pass
`apply_vendor_libs=False` to skip even when deps is present.

Typical pipeline order:

```text
materialize_sources(job_paths, sources)  # M3.1
  → writes project/ + foundry.toml
  → optional apply_default_vendor_libs(project)  # M3.2
forge build  (network deny; libs already vendored)
```

M3.2 should **not** re-validate source paths; it receives an already-materialized
`project/` directory (or is invoked via the hook above).

## Public API

```python
from auditor.contracts import JobPaths
from auditor.ingest import materialize_sources, IngestLimits

paths = JobPaths(job_root=root, job_id=job_id)
paths.ensure_skeleton()
result = materialize_sources(
    paths,
    {"src/A.sol": "pragma solidity ^0.8.20; contract A {}"},
)
# result.project_dir, result.files_written, result.pragma_info, …
```

Typed errors: `PathTraversalError`, `PayloadTooLargeError`,
`PragmaConflictError`, `InvalidFoundryConfigError`, `MaterializeError`.

---

## Gist fetch (M3.3)

Remote gist import is **off by default**. The job runner must pass
`NetworkPolicy.ALLOW_FETCH` for a short fetch phase only, then return to
`NetworkPolicy.DENY` for compile / static / tests. See
[`NetworkPolicy`](../src/auditor/security/config.py) and
[security/threat-model.md](./security/threat-model.md) (A4 SSRF / exfil).

### API

```python
from auditor.ingest import fetch_gist_sources, materialize_sources
from auditor.security import NetworkPolicy

sources = fetch_gist_sources(
    request.gist_url,
    dest_dir=job_paths.resolve("artifacts/source/gist"),
    network_policy=NetworkPolicy.ALLOW_FETCH,  # required on cold cache
    timeout_seconds=30.0,       # optional
    max_total_bytes=2_000_000,  # optional; aligned with IngestLimits
)
# No re-fetch mid-pipeline: use `sources` or the on-disk cache.
result = materialize_sources(job_paths, sources)
```

### Fail closed

| Policy | Cold cache | Warm complete cache |
|--------|------------|---------------------|
| `deny` (default) | **`FetchNotAllowedError`** — **no** network | Return map from disk (no network) |
| `allow_llm` | **`FetchNotAllowedError`** | Return map from disk |
| `allow_fetch` | `GET https://api.github.com/gists/{id}` | Return map from disk |

Warm cache allows “fetch once → deny forever”: later phases keep
`NetworkPolicy.DENY` and still re-read cached sources if needed.

Orchestrators should prefer the single process knob (`NetworkPolicy` /
`AUDIT_NETWORK_POLICY`) rather than a second “allow gist” env flag.

### Transport

- Stdlib `urllib` only (no httpx/requests dependency).
- Headers: `Accept: application/vnd.github+json`, `User-Agent: auditor-engine`,
  `X-GitHub-Api-Version: 2022-11-28`.
- Timeout + HTTP body size cap; per-file and total source UTF-8 caps.
- Truncated gist files may trigger a second HTTPS GET to `raw_url` (still bounded).
- **CI / unit tests must not hit the real network** (mock `urllib.request.urlopen`).

### URL shapes

- `https://gist.github.com/{user}/{id}` (+ optional `/raw/...`)
- `https://gist.github.com/{id}`
- `https://gist.githubusercontent.com/{user}/{id}/raw/...`
- `https://api.github.com/gists/{id}`
- bare hex gist id

Bare gist filenames become `src/{name}` (e.g. `Foo.sol` → `src/Foo.sol`).

### Cache layout

Under `dest_dir` (e.g. `artifacts/source/gist/`):

```text
dest_dir/
  .gist_cache.json    # gist_id + file list
  src/Foo.sol
  lib/Bar.sol
```

Writes use temp file + `os.replace`. Completeness is checked via meta + files.

### Gist-specific env knobs

| Variable | Default | Meaning |
|----------|---------|---------|
| `AUDIT_GIST_FETCH_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `AUDIT_GIST_MAX_BYTES` | `2000000` | Total source UTF-8 bytes (fetch stage) |

Per-file default matches `AUDIT_MAX_FILE_BYTES` / `IngestLimits` (512000).

### Gist errors

| Exception | When |
|-----------|------|
| `FetchNotAllowedError` | Policy ≠ `allow_fetch` and cache incomplete |
| `InvalidGistUrlError` | Unparseable URL |
| `PayloadTooLargeError` | Total / per-file / HTTP body cap (shared with M3.1) |
| `GistFetchError` | HTTP/API/content failure (`status` when applicable) |

All fetch-specific types subclass `FetchError` except shared
`PayloadTooLargeError`.

### Job runner sketch

```text
1. Create job workdir / JobPaths.ensure_skeleton()
2. If request.sources → sources = request.sources
   Elif request.gist_url →
     sources = fetch_gist_sources(..., network_policy=ALLOW_FETCH)
3. materialize_sources(job_paths, sources)   # offline
4. Remaining stages with NetworkPolicy.DENY
```

Container runtimes should use `--network=none` for most of the job; only the
short-lived fetch phase needs egress to `api.github.com` /
`gist.githubusercontent.com`.
