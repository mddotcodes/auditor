# Batch audit fixtures (multi-contract calibration)

Used for full container dual-LLM runs across **different** shapes (not only reentrancy).

| # | Contract | Shape | Expectation (static) |
|---|----------|--------|----------------------|
| 1 | `CleanCounter.sol` | Simple clean | Few/no security highs |
| 2 | `OzToken.sol` (→ examples/contracts) | Clean OZ ERC20+Ownable | Clean / style only |
| 3 | `SafeCounter.sol` | Simple owned counter | Clean-ish |
| 4 | `Unprotected.sol` | Access control | Access / privilege issues |
| 5 | `UncheckedCall.sol` | Unchecked call | Unchecked low-level call |
| 6 | `MissingReturns.sol` | Weird ERC20 | Compile; weak static |
| 7 | `TransferFee.sol` | Weird ERC20 fee | Compile; weak static |
| 8 | `ReentrantToken.sol` | Weird ERC20 callback | Possible reentrancy-related |
| 9 | `SimpleVault.sol` | Medium DeFi-ish | Access / drain patterns |
| 10 | `MultiSigLite.sol` | Multi-function | Mixed; not pure reentrancy bank |

Reentrancy bank fixtures are **excluded** (already calibrated).
