# Test reports

This directory holds **human-readable summaries** of test runs for PRs, local debugging, and (optionally) CI artifacts.

## Purpose

- Give contributors a short pass/fail snapshot without scrolling full pytest logs.
- Document the **report format** (see [sample-static-corpus.md](./sample-static-corpus.md) for a fictional static-corpus style summary).
- Avoid cluttering git history with every CI run: prefer **uploading** `latest.md` as a workflow artifact.

## Files

| File | Role |
|------|------|
| `latest.md` | Overwritten by `scripts/gen-test-report.sh` / `make test-report` |
| `sample-static-corpus.md` | Checked-in **sample** of a static corpus summary (fictional; not live CI output) |

Generated `latest.md` may be gitignored or left uncommitted. Do not treat it as a release artifact.

## Regenerate

From the repo root (dev install required: `make install`):

```bash
make test-report
```

Or:

```bash
./scripts/gen-test-report.sh
./scripts/gen-test-report.sh -q --tb=no    # default-like flags are already set; extra args are appended
./scripts/gen-test-report.sh -k parsers   # filter tests
```

The script runs `python -m pytest --tb=no -q` (plus any extra args), best-effort parses pass/fail counts, writes `docs/test-reports/latest.md`, and echoes the path written.

## CI

The default CI job may upload `docs/test-reports/latest.md` as a workflow artifact after tests (see `.github/workflows/ci.yml`). Download from the Actions run page rather than committing every report.

If artifact upload is not configured:

1. Run `make test-report` locally or in a workflow step.
2. Upload `docs/test-reports/latest.md` manually via the Actions UI or `actions/upload-artifact`.

## Related

- [testing.md](../testing.md) — unit / integration / corpus / container methodology
