# Bundled vendor packs

Pinned third-party Solidity libraries for **offline** Foundry builds (strict dependency mode).

| Directory | Upstream | Pin file |
|-----------|----------|----------|
| `forge-std/` | [foundry-rs/forge-std](https://github.com/foundry-rs/forge-std) | `forge-std/VERSION` |
| `openzeppelin-contracts/` | [OpenZeppelin/openzeppelin-contracts](https://github.com/OpenZeppelin/openzeppelin-contracts) | `openzeppelin-contracts/VERSION` |

Do not run `forge install` inside jobs by default — the engine copies these trees into `project/lib/`. See [docs/dependencies.md](../docs/dependencies.md).

## Refreshing pins

```bash
# Example: bump forge-std
VER=v1.9.6
curl -fsSL "https://github.com/foundry-rs/forge-std/archive/refs/tags/${VER}.tar.gz" | tar -xz -C /tmp
rm -rf vendor/forge-std && mkdir -p vendor/forge-std
cp -a /tmp/forge-std-*/src vendor/forge-std/
echo "$VER" > vendor/forge-std/VERSION
```

Keep licenses intact (MIT for both packs at time of vendoring).
