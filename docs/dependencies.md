# Dependencies & remappings (offline-first)

Auditor materializes each job as a Foundry project under `project/`. Third-party Solidity libraries are resolved **without network access** in the default **strict** mode.

## Policy summary

| Mode | Remote `forge install` / git | Bundled vendor | User-supplied `lib/` |
|------|------------------------------|----------------|----------------------|
| **`strict` (default)** | **Denied** | Copied into `lib/` when enabled | Allowed (bring-your-own) |
| `allowlist` | Reserved (not implemented) | Same | Allowed |
| `permissive` | Rejected at config load | — | — |

Environment:

| Variable | Default | Meaning |
|----------|---------|---------|
| `AUDIT_DEPENDENCY_MODE` | `strict` | See above |
| `AUDIT_ALLOW_REMOTE_INSTALL` | `false` | Ignored/forced off in strict mode |
| `AUDIT_AUTO_VENDOR` | `true` | Copy engine-bundled packs into `project/lib/` |
| `AUDIT_AUTO_VENDOR_PACKS` | `forge-std,openzeppelin-contracts` | Comma-separated pack names |
| `AUDIT_VENDOR_ROOT` | auto | Override path to the `vendor/` tree |

API:

```python
from pathlib import Path
from auditor.deps import DependencyPolicy, apply_default_vendor_libs

result = apply_default_vendor_libs(
    Path("project"),
    policy=DependencyPolicy.from_env(),
)
print(result.installed, result.remappings)
```

Attempting a remote install while strict raises `PermissionError` via
`DependencyPolicy.assert_remote_install_allowed(...)`.

## Bundled vendor packs

Shipped under repository `vendor/` (copied into the Docker image at build time):

| Pack | Import prefixes | Layout under `lib/` |
|------|-----------------|---------------------|
| `forge-std` | `forge-std/` | `lib/forge-std/src/...` |
| `openzeppelin-contracts` | `@openzeppelin/contracts/` | `lib/openzeppelin-contracts/contracts/...` |

Pinned versions are recorded in each pack’s `VERSION` file.

Remappings written to `remappings.txt` and `foundry.toml`:

```text
forge-std/=lib/forge-std/src/
@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/
openzeppelin-contracts/=lib/openzeppelin-contracts/contracts/
```

## Bring-your-own `lib/`

For libraries not bundled (or to pin your own OZ revision):

1. Include files in the audit `sources` map under `lib/<name>/...`, **or**
2. Mount/copy a directory into the job’s `project/lib/` before compile.

Examples:

```text
lib/my-vault/src/Vault.sol
lib/solmate/src/tokens/ERC20.sol
```

Add matching remappings in a user-supplied `foundry.toml` or `remappings.txt` in the sources map. Engine auto-vendor **will not delete** unknown `lib/*` directories; it only manages known pack names when `force=False` (skips if the pack dir already exists).

### Offline OZ-style project

```solidity
// src/Token.sol
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract Token is ERC20 {
    constructor() ERC20("Token", "TKN") {}
}
```

With default auto-vendor, no network is required: OpenZeppelin is copied from `vendor/openzeppelin-contracts`.

## What we deliberately do not do (strict mode)

- `forge install`, `git submodule update`, or curl of arbitrary tarballs during compile
- Resolve remappings to URLs
- Trust LLM-generated install commands

This keeps untrusted jobs from using the engine as a network pivot (see `docs/security/threat-model.md`).

## Docker image

The image build should include the `vendor/` tree (`COPY vendor /opt/auditor/vendor` or repo-root `vendor` with `AUDIT_VENDOR_ROOT`). If packs are missing, `apply_default_vendor_libs` raises `FileNotFoundError` with a pointer here.

## Allowlist (future)

`DependencyMode.ALLOWLIST` will permit `forge install` only for names in a fixed allowlist (e.g. forge-std, openzeppelin-contracts) with pinned tags. Until implemented, selecting allowlist + remote install fails closed.
