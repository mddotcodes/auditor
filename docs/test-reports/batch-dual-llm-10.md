# Batch dual-LLM Docker audit (10 contracts)

Excludes pure reentrancy bank (already calibrated). Docker-only, plan→code dual.

| Contract | Job status | Findings | Security* | Hygiene* | LLM tests | Forge |
|----------|------------|----------|-----------|----------|-----------|-------|
| `clean_counter` | completed | 4 | 0 | 4 | ok@1 | pass |
| `oz_token_clean` | completed | 5 | 0 | 5 | ok@1 | pass |
| `safe_counter` | completed | 6 | 0 | 6 | ok@1 | pass |
| `unprotected_mint` | completed | 7 | 1 | 6 | ok@1 | fail |
| `unchecked_call` | completed | 8 | 3 | 5 | ok@1 | pass |
| `weird_missing_returns` | completed | 12 | 1 | 11 | ok@1 | pass |
| `weird_transfer_fee` | timed_out | 12 | 0 | 12 | fail_format | ? |
| `weird_reentrant_token` | completed | 11 | 0 | 11 | ok@1 | fail |
| `simple_vault` | completed | 11 | 2 | 9 | ok@1 | fail |
| `multisig_lite` | completed | 7 | 1 | 6 | fail_format | fail |

\*Rough tier heuristic for this report (not yet product code).

## Security findings by contract

### clean_counter
- _(none in security tier)_

### oz_token_clean
- _(none in security tier)_

### safe_counter
- _(none in security tier)_

### unprotected_mint
- `slither:erc20-interface(medium)`

### unchecked_call
- `slither:unchecked-lowlevel(medium)`
- `aderyn:eth-send-unchecked-address(high)`
- `aderyn:unchecked-low-level-call(high)`

### weird_missing_returns
- `slither:erc20-interface(medium)`

### weird_transfer_fee
- _(none in security tier)_

### weird_reentrant_token
- _(none in security tier)_

### simple_vault
- `slither:arbitrary-send-eth(high)`
- `aderyn:eth-send-unchecked-address(high)`

### multisig_lite
- `aderyn:eth-send-unchecked-address(high)`

## Process notes
- Compile: all cases that finished static succeeded (OzToken used vendor OZ).
- Clean contracts still emit hygiene findings (solc-version, events, pragma).
- LLM: FILE-block / fence issues cause retries; one timeout on transfer_fee.
- Forge fail often test quality (assertions/ETH plumbing), not static miss.
