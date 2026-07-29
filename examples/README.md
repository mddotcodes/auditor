# Examples

Sample inputs and artifact shapes for local development. Self-contained Solidity (no imports) so demos stay offline-friendly.

## Contracts

| File | Purpose |
|------|---------|
| [`contracts/VulnerableBank.sol`](./contracts/VulnerableBank.sol) | Small bank with a classic reentrancy-friendly `withdraw` (external call before balance clear). Useful later for Slither / fuzz demos. |
| [`contracts/SafeCounter.sol`](./contracts/SafeCounter.sol) | Trivial owned counter — relatively clean baseline. |

Pragma: `^0.8.20`, SPDX MIT. No external libraries.

## Artifacts

| File | Purpose |
|------|---------|
| [`artifacts/manifest.example.json`](./artifacts/manifest.example.json) | Example `artifacts/manifest.json` aligned with schema version **1** (see [`schemas/`](../schemas/)). |

Canonical contracts: [`schemas/artifact-manifest.schema.json`](../schemas/artifact-manifest.schema.json), OpenAPI [`schemas/openapi.yaml`](../schemas/openapi.yaml). The HTTP server is not implemented yet (Phase 5); models live in `auditor.contracts`.

## Running the image against samples (later)

Once job runners exist, expect something like mounting `examples/contracts` into `/work`. Today, verify the image with:

```bash
./scripts/run-local.sh versions
# or
docker compose run --rm auditor versions
```
