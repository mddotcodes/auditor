# Vulnerability corpus (curated)

Small, offline Solidity fixtures for demos and regression. **Not** full upstream repos.

| Directory | Class | Origin (inspired by) |
|-----------|--------|----------------------|
| `reentrancy/` | Classic external-call-before-state-update | crytic/not-so-smart-contracts, our VulnerableBank |
| `unprotected_function/` | Missing access control on privileged op | not-so-smart-contracts |
| `unchecked_call/` | Ignoring low-level call success | not-so-smart-contracts |
| `weird_erc20/` | Hostile/edge ERC20 semantics (2–3 tokens) | d-xo/weird-erc20 (minimal ports) |

Each folder has:

- `src/*.sol` — contract(s)
- `expected.json` — outcome **families** (not exact Slither dump equality)
- `README.md` — one-liner

## Non-goals

- Mainnet forks / Cream-style exploit repros  
- Full weird-erc20 or honeypot trees  
- LLM-required checks in CI  

## Manual first LLM run (you control tokens)

After static corpus is green:

```bash
set -a && source .env && set +a
auditor-cli run examples/corpus/reentrancy/src/ReentrancyBank.sol \
  --profile default \
  --job-root ./work/jobs
```

Inspect `work/jobs/<id>/artifacts/` before running more contracts.
