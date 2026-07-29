# Sample: static corpus run summary

> **Fictional / illustrative.** Not produced by CI. Shows the intended shape of a static-corpus style report once fixtures under `examples/corpus/` or `tests/fixtures/vuln/` exist.

**Generated:** 2026-07-29T12:00:00Z (sample)  
**Profile:** `static` (offline)  
**Tools:** Slither (when available); no LLM; no RPC  

## Suite

| Field | Value |
|-------|--------|
| Command | `python -m pytest -q --tb=no -k corpus` (example) |
| Result | **passed** |
| Passed | 12 |
| Failed | 0 |
| Skipped | 2 (Slither not on PATH in one matrix cell) |
| Duration | ~4.2s |

## Corpus fixtures (example)

| Fixture | Expected family | Observed | Status |
|---------|-----------------|----------|--------|
| `reentrancy` / VulnerableBank-style | reentrancy | reentrancy-eth, reentrancy-no-eth | ok |
| `unprotected_function` | unprotected-upgrade / missing-zero-check | unprotected-upgrade | ok |
| `weird_erc20/missing_return` | (compile + weak static) | compile ok | ok |

## Notes

- Assertions use fixture `expected.json` (`min_findings`, `tool_any_of`, `detector_families[]`) when present.
- Skips are acceptable when optional tools are missing; failures require unexpected missing families or crash.
- Full logs remain in the pytest/CI job log; this file is a short human summary only.

## Related

- [README.md](./README.md) — how to regenerate `latest.md`
- [../testing.md](../testing.md) — methodology
